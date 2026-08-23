"""测试场景生成工作流（T7 - Issue #19）。

测试要点：
1. 端到端工作流可执行
2. SceneArtifact 原子提交（InteractionZone + render asset）
3. 最终场景重试逻辑（最多 3 attempts）
4. 内部资产不暴露给 Unity
5. Scene ready 前 Unity 不可见
"""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from agent_service.storage.destination_storage import DestinationRepository
from agent_service.storage import Storage
from agent_service.workflows.scene_generation import (
    run_scene_generation_workflow,
    build_scene_generation_workflow,
    SceneGenerationState,
)
from agent_service.shared.ids import new_id
from agent_service.tests.helpers.simple_file_storage import SimpleFileStorage


# ============================================================================
# 测试 Fixture
# ============================================================================


@pytest.fixture
def temp_storage():
    """创建临时存储。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        files_dir = Path(tmpdir) / "files"

        # 初始化 pilot4mvp2 基座
        storage = Storage(db_path, recover=False)
        client_id = storage.upsert_api_client("test_hash", "test_client")
        session = storage.create_session(client_id)

        # 初始化目的地 Repository
        repo = DestinationRepository(db_path)
        repo.open()

        # 初始化文件存储（使用简化版本）
        file_storage = SimpleFileStorage(files_dir)

        yield {
            "repo": repo,
            "file_storage": file_storage,
            "storage": storage,
            "session": session,
            "client_id": client_id,
        }

        repo.close()
        storage.close()


@pytest.fixture
def sample_environment_image() -> bytes:
    """创建 2048x1152 的测试环境图。"""
    img = Image.new("RGB", (2048, 1152), color=(100, 150, 200))
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def setup_destination_with_environment(temp_storage, sample_environment_image):
    """设置带有共享环境的目的地。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]
    session = temp_storage["session"]
    client_id = temp_storage["client_id"]

    # 1. 创建目的地
    destination = repo.create_destination(
        session_id=session["id"], api_client_id=client_id
    )

    # 2. 创建 Requirements（简化）
    requirements = repo.create_destination_requirements(
        destination_id=destination["id"],
        source_inputs=[],
        sha256="test_req_hash",
    )

    # 3. 创建 DestinationSpec（简化）
    spec = repo.create_destination_spec(
        destination_id=destination["id"],
        requirements_id=requirements["requirements_id"],
        template_id="test_template",
        template_version="v1",
        title="测试目的地",
        shared_environment_spec={"test": "spec"},
        spec_version=1,
        requirements_sha256="test_req_hash",
        sha256="test_spec_hash",
    )

    # 4. 创建 ScenePlan
    scene_a = repo.create_scene_plan(
        destination_id=destination["id"],
        spec_id=spec["spec_id"],
        order_index=0,
        state_label="探索",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        semantic_anchor="木屋前的空地",
        interaction_prompt="点击宠物",
    )

    # 5. 创建共享环境 artifact
    import hashlib

    env_sha256 = hashlib.sha256(sample_environment_image).hexdigest()
    env_file_id = new_id("file")

    # 使用简化的 file_storage.write() 存储文件
    file_storage.write(env_file_id, sample_environment_image, "image/png")

    # 在 Storage 中注册文件（满足外键约束）
    temp_storage["storage"].create_file(
        file_id=env_file_id,
        api_client_id=client_id,
        source="agent_generated",
        purpose="generated_image",
        mime_type="image/png",
        size_bytes=len(sample_environment_image),
        sha256=env_sha256,
        width=2048,
        height=1152,
        rel_path=f"{env_file_id}.dat",
    )

    # 创建一个 run（满足 source_run_id 外键约束）
    run = temp_storage["storage"].create_run(
        session_id=session["id"],
        api_client_id=client_id,
        request_input={},
        response_format={"type": "json_object"},
        idempotency_key=new_id("idem"),
        idempotency_body_hash=hashlib.sha256(b"test").hexdigest(),
    )

    # 创建 shared_environment_artifact
    shared_env = repo.create_shared_environment_artifact(
        destination_id=destination["id"],
        source_run_id=run["id"],
        image_file_id=env_file_id,
        image_sha256=env_sha256,
        width_px=2048,
        height_px=1152,
    )

    return {
        "destination": destination,
        "spec": spec,
        "scene": scene_a,
        "shared_environment": shared_env,
    }


# ============================================================================
# 测试用例 1: 端到端工作流
# ============================================================================


def test_scene_generation_workflow_end_to_end(
    temp_storage, setup_destination_with_environment
):
    """测试：场景生成工作流可端到端执行。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]
    storage = temp_storage["storage"]

    destination = setup_destination_with_environment["destination"]
    spec = setup_destination_with_environment["spec"]
    scene = setup_destination_with_environment["scene"]
    shared_env = setup_destination_with_environment["shared_environment"]

    # 运行工作流
    final_state = run_scene_generation_workflow(
        destination_id=destination["id"],
        scene_id=scene["scene_id"],
        spec_id=spec["spec_id"],
        shared_environment_id=shared_env["shared_environment_id"],
        semantic_anchor="木屋前的空地",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        planned_center_x=1024,
        planned_center_y=576,
        interaction_diameter_px=160,
        repo=repo,
        file_storage=file_storage,
        use_mock_final_scene=True,
        storage=storage,
    )

    # 验证成功
    assert final_state["error"] is None
    assert final_state["artifact_ready"] is True
    assert final_state["scene_artifact_id"] is not None
    assert final_state["interaction_zone_id"] is not None


# ============================================================================
# 测试用例 2: SceneArtifact 原子提交
# ============================================================================


def test_scene_artifact_atomic_commit(
    temp_storage, setup_destination_with_environment
):
    """测试：SceneArtifact 原子提交（render asset + InteractionZone + 哈希）。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]

    destination = setup_destination_with_environment["destination"]
    spec = setup_destination_with_environment["spec"]
    scene = setup_destination_with_environment["scene"]
    shared_env = setup_destination_with_environment["shared_environment"]

    final_state = run_scene_generation_workflow(
        destination_id=destination["id"],
        scene_id=scene["scene_id"],
        spec_id=spec["spec_id"],
        shared_environment_id=shared_env["shared_environment_id"],
        semantic_anchor="木屋前的空地",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        planned_center_x=1024,
        planned_center_y=576,
        interaction_diameter_px=160,
        repo=repo,
        file_storage=file_storage,
        use_mock_final_scene=True,
        storage=storage,
    )

    # 验证 SceneArtifact 存在
    artifact = repo.get_scene_artifact(final_state["scene_artifact_id"])
    assert artifact is not None
    assert artifact["render_file_id"] == final_state["final_scene_file_id"]
    assert artifact["render_sha256"] == final_state["final_scene_sha256"]
    assert artifact["interaction_zone_id"] == final_state["interaction_zone_id"]
    assert artifact["shared_environment_sha256"] == shared_env["image_sha256"]

    # 验证 InteractionZone 存在
    zone = repo.get_interaction_zone(final_state["interaction_zone_id"])
    assert zone is not None
    assert zone["center_x_px"] == 1024
    assert zone["center_y_px"] == 576
    assert zone["radius_px"] == 80
    assert zone["coordinate_space"] == "pixel_top_left"


# ============================================================================
# 测试用例 3: 最终场景重试逻辑
# ============================================================================


def test_scene_generation_retries_on_failure(temp_storage, setup_destination_with_environment):
    """测试：最终场景生成失败时重试（最多 3 attempts）。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]

    destination = setup_destination_with_environment["destination"]
    spec = setup_destination_with_environment["spec"]
    scene = setup_destination_with_environment["scene"]
    shared_env = setup_destination_with_environment["shared_environment"]

    # 模拟失败场景：使用不支持的 mock 模式（use_mock_final_scene=False 会失败）
    final_state = run_scene_generation_workflow(
        destination_id=destination["id"],
        scene_id=scene["scene_id"],
        spec_id=spec["spec_id"],
        shared_environment_id=shared_env["shared_environment_id"],
        semantic_anchor="木屋前的空地",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        planned_center_x=1024,
        planned_center_y=576,
        interaction_diameter_px=160,
        repo=repo,
        file_storage=file_storage,
        use_mock_final_scene=False,  # 会触发 NotImplementedError
        storage=storage,
    )

    # 验证失败
    assert final_state["error"] is not None
    assert "final_scene_generation_failed" in final_state["error"]
    assert final_state["scene_generation_attempt"] >= 2  # 至少重试了 2 次


# ============================================================================
# 测试用例 4: 内部资产不暴露给 Unity
# ============================================================================


def test_internal_assets_not_exposed(temp_storage, setup_destination_with_environment):
    """测试：定位图、Mask、aperture 等内部资产不通过 SceneArtifact 暴露。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]

    destination = setup_destination_with_environment["destination"]
    spec = setup_destination_with_environment["spec"]
    scene = setup_destination_with_environment["scene"]
    shared_env = setup_destination_with_environment["shared_environment"]

    final_state = run_scene_generation_workflow(
        destination_id=destination["id"],
        scene_id=scene["scene_id"],
        spec_id=spec["spec_id"],
        shared_environment_id=shared_env["shared_environment_id"],
        semantic_anchor="木屋前的空地",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        planned_center_x=1024,
        planned_center_y=576,
        interaction_diameter_px=160,
        repo=repo,
        file_storage=file_storage,
        use_mock_final_scene=True,
        storage=storage,
    )

    # 获取 SceneArtifact
    artifact = repo.get_scene_artifact(final_state["scene_artifact_id"])

    # 验证只有最终场景被暴露
    assert artifact["render_file_id"] == final_state["final_scene_file_id"]

    # 验证内部资产文件 ID 不在 SceneArtifact 中
    assert artifact["render_file_id"] != final_state["generation_mask_file_id"]
    assert artifact["render_file_id"] != final_state["aperture_file_id"]


# ============================================================================
# 测试用例 5: Mask 与 InteractionZone 一致性
# ============================================================================


def test_mask_and_interaction_zone_consistency(
    temp_storage, setup_destination_with_environment
):
    """测试：Mask 和 InteractionZone 使用相同的 center/radius。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]

    destination = setup_destination_with_environment["destination"]
    spec = setup_destination_with_environment["spec"]
    scene = setup_destination_with_environment["scene"]
    shared_env = setup_destination_with_environment["shared_environment"]

    center_x = 1024
    center_y = 576
    diameter = 160

    final_state = run_scene_generation_workflow(
        destination_id=destination["id"],
        scene_id=scene["scene_id"],
        spec_id=spec["spec_id"],
        shared_environment_id=shared_env["shared_environment_id"],
        semantic_anchor="木屋前的空地",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        planned_center_x=center_x,
        planned_center_y=center_y,
        interaction_diameter_px=diameter,
        repo=repo,
        file_storage=file_storage,
        use_mock_final_scene=True,
        storage=storage,
    )

    # 获取 InteractionZone
    zone = repo.get_interaction_zone(final_state["interaction_zone_id"])

    # 验证 InteractionZone 的几何参数与输入一致
    assert zone["center_x_px"] == center_x
    assert zone["center_y_px"] == center_y
    assert zone["radius_px"] == diameter // 2


# ============================================================================
# 测试用例 6: 圆超出边界失败
# ============================================================================


def test_circle_out_of_bounds_fails(temp_storage, setup_destination_with_environment):
    """测试：圆超出画布边界时工作流失败。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]

    destination = setup_destination_with_environment["destination"]
    spec = setup_destination_with_environment["spec"]
    scene = setup_destination_with_environment["scene"]
    shared_env = setup_destination_with_environment["shared_environment"]

    # 圆心太靠边缘，半径会超出边界
    final_state = run_scene_generation_workflow(
        destination_id=destination["id"],
        scene_id=scene["scene_id"],
        spec_id=spec["spec_id"],
        shared_environment_id=shared_env["shared_environment_id"],
        semantic_anchor="木屋前的空地",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        planned_center_x=50,  # 太靠左
        planned_center_y=576,
        interaction_diameter_px=160,
        repo=repo,
        file_storage=file_storage,
        use_mock_final_scene=True,
        storage=storage,
    )

    # 验证失败
    assert final_state["error"] is not None
    assert "out_of_bounds" in final_state["error"] or "mask_generation_failed" in final_state["error"]


# ============================================================================
# 测试用例 7: pixel_top_left 坐标系统
# ============================================================================


def test_coordinate_space_is_pixel_top_left(
    temp_storage, setup_destination_with_environment
):
    """测试：InteractionZone 使用 pixel_top_left 坐标系统。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]

    destination = setup_destination_with_environment["destination"]
    spec = setup_destination_with_environment["spec"]
    scene = setup_destination_with_environment["scene"]
    shared_env = setup_destination_with_environment["shared_environment"]

    final_state = run_scene_generation_workflow(
        destination_id=destination["id"],
        scene_id=scene["scene_id"],
        spec_id=spec["spec_id"],
        shared_environment_id=shared_env["shared_environment_id"],
        semantic_anchor="木屋前的空地",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        planned_center_x=1024,
        planned_center_y=576,
        interaction_diameter_px=160,
        repo=repo,
        file_storage=file_storage,
        use_mock_final_scene=True,
        storage=storage,
    )

    zone = repo.get_interaction_zone(final_state["interaction_zone_id"])
    assert zone["coordinate_space"] == "pixel_top_left"


# ============================================================================
# 测试用例 8: Scene ready 后才可见
# ============================================================================


def test_scene_not_visible_until_ready(temp_storage, setup_destination_with_environment):
    """测试：SceneArtifact ready 之前，Unity 不可见。"""
    repo = temp_storage["repo"]
    file_storage = temp_storage["file_storage"]
    storage = temp_storage["storage"]

    destination = setup_destination_with_environment["destination"]
    spec = setup_destination_with_environment["spec"]
    scene = setup_destination_with_environment["scene"]
    shared_env = setup_destination_with_environment["shared_environment"]

    # 在工作流完成前，SceneArtifact 不应存在
    artifacts_before = repo.list_scene_artifacts(destination["id"])
    assert len(artifacts_before) == 0

    # 运行工作流
    final_state = run_scene_generation_workflow(
        destination_id=destination["id"],
        scene_id=scene["scene_id"],
        spec_id=spec["spec_id"],
        shared_environment_id=shared_env["shared_environment_id"],
        semantic_anchor="木屋前的空地",
        pet_behavior="四处张望",
        pet_emotion="好奇",
        planned_center_x=1024,
        planned_center_y=576,
        interaction_diameter_px=160,
        repo=repo,
        file_storage=file_storage,
        use_mock_final_scene=True,
        storage=storage,
    )

    # 工作流完成后，SceneArtifact 才可见
    artifacts_after = repo.list_scene_artifacts(destination["id"])
    assert len(artifacts_after) == 1
    assert artifacts_after[0]["scene_artifact_id"] == final_state["scene_artifact_id"]
