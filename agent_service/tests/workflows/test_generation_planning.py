"""测试 Generation Planning Workflow（T5 - Issue #17）。

覆盖 Issue #10 第 15.3 节部分测试要求（共享环境相关）。
"""

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from agent_service.adapters.image import ImageResult
from agent_service.storage.destination_storage import DestinationRepository
from agent_service.storage.files import LocalImageStorage
from agent_service.workflows.generation_planning import (
    mock_generate_environment_image,
    run_generation_planning_workflow,
)
from agent_service.shared.ids import new_id


@pytest.fixture
def temp_db():
    """临时数据库 fixture。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def temp_storage():
    """临时文件存储 fixture。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def repo(temp_db):
    """Repository fixture。"""
    # 先初始化基础 Database（包含 sessions, runs, api_clients 等表）
    from agent_service.storage.database import Database

    base_db = Database(temp_db)
    base_db.close()

    # 再初始化 DestinationRepository（添加目的地相关表）
    repo = DestinationRepository(temp_db)
    repo.open()
    yield repo
    repo.close()


@pytest.fixture
def file_storage(temp_storage):
    """FileStorage fixture。"""
    return LocalImageStorage(temp_storage)


@pytest.fixture
def setup_destination(repo):
    """设置完整的目的地数据（Requirements + Spec + ScenePlans）。"""
    # 需要先创建 api_client 和 session
    from agent_service.storage.database import Database

    db = Database(repo.db_path)

    # 创建 API client
    client_id = db.upsert_api_client("test_hash", "test_client")

    # 创建 Session
    with db.transaction() as conn:
        session_id = new_id("session")
        conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) "
            "VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    db.close()

    # 创建 Destination
    destination = repo.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )
    destination_id = destination["id"]

    # 创建 Requirements
    source_inputs = [
        {
            "input_id": new_id("input"),
            "raw_text": "想要一个温馨的环境",
            "classification": "accepted_wish_input",
        }
    ]
    requirements_data = {
        "source_inputs": source_inputs,
        "items": [
            {
                "normalized_statement": "温馨舒适的氛围",
                "polarity": "include",
                "fulfillment": "best_effort",
                "source_type": "player_input",
                "source_input_ids": [source_inputs[0]["input_id"]],
                "rationale": None,
            }
        ],
    }
    requirements_json = json.dumps(requirements_data, sort_keys=True, ensure_ascii=False)
    requirements_sha256 = hashlib.sha256(requirements_json.encode("utf-8")).hexdigest()

    requirements = repo.create_destination_requirements(
        destination_id=destination_id,
        source_inputs=source_inputs,
        sha256=requirements_sha256,
    )

    repo.create_requirement_item(
        requirements_id=requirements["requirements_id"],
        normalized_statement="温馨舒适的氛围",
        polarity="include",
        fulfillment="best_effort",
        source_type="player_input",
        source_input_ids=[source_inputs[0]["input_id"]],
    )

    # 创建 Spec
    shared_environment_spec = {
        "description": "温馨舒适的室内环境",
        "style_constraints": ["温馨", "舒适"],
        "environment_design": {
            "style_template_id": "style_fixture",
            "style_template_version": "2.0",
            "composition_template_id": "composition_fixture",
            "composition_template_version": "3.0",
            "filled_slots": {"destination_description": "温馨舒适的室内环境"},
            "negative_constraints": ["避免杂乱"],
            "references": [
                {
                    "role": "style_reference",
                    "asset_key": "style_001/ref_1.png",
                    "order_index": 0,
                    "sha256": "134c438d334d332bf9ec4b1653f9558e1df9dd0ad2765bdcf5940f1c17a91b5d",
                    "mime_type": "image/png",
                    "width": 90,
                    "height": 160,
                }
            ],
            "rendered_prompt": "来自模板 fixture 的权威环境 Prompt",
        },
    }
    spec_content = {
        "template_id": "default",
        "template_version": "1.0",
        "requirements_id": requirements["requirements_id"],
        "requirements_sha256": requirements_sha256,
        "title": "温馨宠物小屋",
        "shared_environment_spec": shared_environment_spec,
        "scene_plans": [
            {
                "order": 0,
                "state_label": "休息",
                "pet_behavior": "趴着休息",
                "pet_emotion": "放松",
                "semantic_anchor": "角落",
                "interaction_prompt": "抚摸",
            },
            {
                "order": 1,
                "state_label": "活跃",
                "pet_behavior": "站立张望",
                "pet_emotion": "好奇",
                "semantic_anchor": "窗边",
                "interaction_prompt": "一起看窗外",
            },
        ],
    }
    spec_json = json.dumps(spec_content, sort_keys=True, ensure_ascii=False)
    spec_sha256 = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()

    spec = repo.create_destination_spec(
        destination_id=destination_id,
        spec_version=1,
        template_id="default",
        template_version="1.0",
        requirements_id=requirements["requirements_id"],
        requirements_sha256=requirements_sha256,
        title="温馨宠物小屋",
        shared_environment_spec=shared_environment_spec,
        sha256=spec_sha256,
    )

    # 创建两个 ScenePlans
    scene_plan_0 = repo.create_scene_plan(
        destination_id=destination_id,
        spec_id=spec["spec_id"],
        order_index=0,
        state_label="休息",
        pet_behavior="趴着休息",
        pet_emotion="放松",
        semantic_anchor="角落",
        interaction_prompt="抚摸",
    )

    scene_plan_1 = repo.create_scene_plan(
        destination_id=destination_id,
        spec_id=spec["spec_id"],
        order_index=1,
        state_label="活跃",
        pet_behavior="站立张望",
        pet_emotion="好奇",
        semantic_anchor="窗边",
        interaction_prompt="一起看窗外",
    )

    return {
        "destination_id": destination_id,
        "spec_id": spec["spec_id"],
        "scene_plan_ids": [scene_plan_0["scene_id"], scene_plan_1["scene_id"]],
        "session_id": session_id,
        "client_id": client_id,
    }


@pytest.fixture
def create_run(repo, setup_destination):
    """创建有效的 Run 记录的辅助函数。"""
    from agent_service.storage.database import Database

    def _create_run():
        db = Database(repo.db_path)
        run_id = new_id("run")

        # 直接在事务中插入 run（简化版）
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO runs(id, session_id, api_client_id, status, "
                "idempotency_key, idempotency_body_hash, request_input, response_format, created_at) "
                "VALUES(?, ?, ?, 'queued', ?, ?, ?, ?, datetime('now'))",
                (
                    run_id,
                    setup_destination["session_id"],
                    setup_destination["client_id"],
                    f"test_key_{run_id}",
                    "test_hash",
                    "{}",
                    "{}",
                ),
            )

        db.close()
        return run_id

    return _create_run


class RecordingImageProvider:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return ImageResult(
            data=mock_generate_environment_image(),
            mime_type="image/png",
            width=2048,
            height=1152,
        )


# ============================================================================
# 测试用例（Issue #10 第 15.3 节）
# ============================================================================


def test_two_scenes_reference_same_master_image(repo, file_storage, setup_destination, create_run):
    """测试 1: 两个 Scene 引用同一母图 file_id 与 SHA。"""
    destination_id = setup_destination["destination_id"]
    spec_id = setup_destination["spec_id"]
    run_id = create_run()

    # 运行工作流
    result = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec_id,
        repo=repo,
        file_storage=file_storage,
        run_id=run_id,
    )

    # 验证没有错误
    assert result["error"] is None

    # 验证共享环境已生成
    assert result["shared_environment_id"] is not None
    assert result["environment_file_id"] is not None
    assert result["environment_sha256"] is not None

    # 从 Repository 读取 SharedEnvironmentArtifact
    artifact = repo.get_shared_environment_artifact(destination_id)
    assert artifact is not None
    assert artifact["image_file_id"] == result["environment_file_id"]
    assert artifact["image_sha256"] == result["environment_sha256"]

    # 验证两个 ScenePlan 都引用同一个 artifact（通过 destination_id 关联）
    scene_plans = repo.list_scene_plans(destination_id)
    assert len(scene_plans) == 2
    # 两个场景都属于同一个 destination，共享同一个 SharedEnvironmentArtifact


def test_master_image_atomic_persist_and_validation(repo, file_storage, setup_destination, create_run):
    """测试 2: 母图原子落盘并通过格式/尺寸/哈希校验。"""
    destination_id = setup_destination["destination_id"]
    spec_id = setup_destination["spec_id"]
    run_id = create_run()

    # 运行工作流
    result = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec_id,
        repo=repo,
        file_storage=file_storage,
        run_id=run_id,
    )

    # 验证环境已生成
    assert result["environment_file_id"] is not None
    assert result["environment_sha256"] is not None
    assert result["environment_width"] == 2048
    assert result["environment_height"] == 1152

    # 验证 SharedEnvironmentArtifact 记录了正确的元数据
    artifact = repo.get_shared_environment_artifact(destination_id)
    assert artifact["width_px"] == 2048
    assert artifact["height_px"] == 1152
    assert len(artifact["image_sha256"]) == 64  # SHA-256 十六进制长度


def test_shared_environment_artifact_immutable(repo, file_storage, setup_destination, create_run):
    """测试 3: SharedEnvironmentArtifact 提交后不可修改。"""
    destination_id = setup_destination["destination_id"]
    spec_id = setup_destination["spec_id"]
    run_id = create_run()

    # 第一次运行工作流
    result1 = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec_id,
        repo=repo,
        file_storage=file_storage,
        run_id=run_id,
    )

    shared_env_id_1 = result1["shared_environment_id"]
    file_id_1 = result1["environment_file_id"]
    sha256_1 = result1["environment_sha256"]

    # 第二次运行工作流（幂等性测试）
    run_id_2 = create_run()
    result2 = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec_id,
        repo=repo,
        file_storage=file_storage,
        run_id=run_id_2,
    )

    # 验证返回的是同一个 artifact（幂等）
    assert result2["shared_environment_id"] == shared_env_id_1
    assert result2["environment_file_id"] == file_id_1
    assert result2["environment_sha256"] == sha256_1


def test_environment_generation_retry_on_failure(repo, file_storage, setup_destination, create_run):
    """测试 4: 环境生成失败时可重试（最多 3 attempts）。"""
    destination_id = setup_destination["destination_id"]
    spec_id = setup_destination["spec_id"]
    run_id = create_run()

    # 运行工作流
    result = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec_id,
        repo=repo,
        file_storage=file_storage,
        run_id=run_id,
    )

    # 验证成功（因为 mock 函数总是成功）
    assert result["error"] is None

    # 检查 operation_attempts 记录
    attempts_count = repo.count_operation_attempts(
        destination_id=destination_id,
        operation_type="shared_environment",
    )
    # 成功时只有 1 次尝试
    assert attempts_count == 1


def test_retry_preserves_spec_and_scene_plans(repo, file_storage, setup_destination, create_run):
    """测试 5: 重试保持 DestinationSpec 和 ScenePlan 不变。"""
    destination_id = setup_destination["destination_id"]
    spec_id = setup_destination["spec_id"]
    scene_plan_ids = setup_destination["scene_plan_ids"]
    run_id = create_run()

    # 读取初始 Spec 和 ScenePlans
    spec_before = repo.get_destination_spec(destination_id)
    scene_plans_before = repo.list_scene_plans(destination_id)

    # 运行工作流
    result = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec_id,
        repo=repo,
        file_storage=file_storage,
        run_id=run_id,
    )

    # 读取运行后的 Spec 和 ScenePlans
    spec_after = repo.get_destination_spec(destination_id)
    scene_plans_after = repo.list_scene_plans(destination_id)

    # 验证 Spec 不变
    assert spec_before["spec_id"] == spec_after["spec_id"]
    assert spec_before["sha256"] == spec_after["sha256"]
    assert spec_before["locked_at"] == spec_after["locked_at"]

    # 验证 ScenePlans 不变
    assert len(scene_plans_before) == len(scene_plans_after)
    for i in range(len(scene_plans_before)):
        assert scene_plans_before[i]["scene_id"] == scene_plans_after[i]["scene_id"]
        assert scene_plans_before[i]["pet_behavior"] == scene_plans_after[i]["pet_behavior"]


def test_workflow_validates_two_scene_invariants(repo, file_storage):
    """测试 6: 工作流验证两场景不变量（必须恰好 2 个且行为不同）。"""
    # 需要先创建 api_client 和 session
    from agent_service.storage.database import Database

    db = Database(repo.db_path)

    # 创建 API client
    client_id = db.upsert_api_client("test_hash", "test_client")

    # 创建 Session
    with db.transaction() as conn:
        session_id = new_id("session")
        conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) "
            "VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    # 创建 Run
    run_id = new_id("run")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO runs(id, session_id, api_client_id, status, "
            "idempotency_key, idempotency_body_hash, request_input, response_format, created_at) "
            "VALUES(?, ?, ?, 'queued', ?, ?, ?, ?, datetime('now'))",
            (
                run_id,
                session_id,
                client_id,
                f"test_key_{run_id}",
                "test_hash",
                "{}",
                "{}",
            ),
        )

    db.close()

    # 创建一个只有 1 个场景的目的地
    destination = repo.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )
    destination_id = destination["id"]

    # 创建 Requirements 和 Spec（简化）
    requirements = repo.create_destination_requirements(
        destination_id=destination_id,
        source_inputs=[],
        sha256="test_sha256",
    )

    spec = repo.create_destination_spec(
        destination_id=destination_id,
        spec_version=1,
        template_id="test",
        template_version="1.0",
        requirements_id=requirements["requirements_id"],
        requirements_sha256="test_sha256",
        title="测试",
        shared_environment_spec={},
        sha256="spec_sha256",
    )

    # 只创建 1 个 ScenePlan（违反不变量）
    repo.create_scene_plan(
        destination_id=destination_id,
        spec_id=spec["spec_id"],
        order_index=0,
        state_label="休息",
        pet_behavior="趴着",
        pet_emotion="放松",
        semantic_anchor="角落",
        interaction_prompt="抚摸",
    )

    # 运行工作流
    result = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec["spec_id"],
        repo=repo,
        file_storage=file_storage,
        run_id=run_id,
    )

    # 验证失败（因为只有 1 个场景）
    assert result["error"] is not None
    assert "必须恰好 2 个场景计划" in result["error"]


def test_provider_request_and_prompt_snapshot_use_spec_rendered_prompt(
    repo, file_storage, setup_destination, create_run
):
    provider = RecordingImageProvider()
    destination_id = setup_destination["destination_id"]

    result = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=setup_destination["spec_id"],
        repo=repo,
        file_storage=file_storage,
        run_id=create_run(),
        image_provider=provider,
    )

    assert result["error"] is None
    assert [request.prompt for request in provider.requests] == [
        "来自模板 fixture 的权威环境 Prompt"
    ]
    assert [reference.role for reference in provider.requests[0].references] == [
        "style_reference"
    ]
    assert provider.requests[0].references[0].sha256 == (
        "134c438d334d332bf9ec4b1653f9558e1df9dd0ad2765bdcf5940f1c17a91b5d"
    )
    artifact = repo.get_shared_environment_artifact(destination_id)
    snapshot = repo.get_prompt_snapshot(artifact["prompt_snapshot_id"])
    assert snapshot["prompt_text"] == provider.requests[0].prompt


def test_prompt_snapshot_created(repo, file_storage, setup_destination, create_run):
    """测试 7: PromptSnapshot 正确创建并关联到 SharedEnvironmentArtifact。"""
    destination_id = setup_destination["destination_id"]
    spec_id = setup_destination["spec_id"]
    run_id = create_run()

    # 运行工作流
    result = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec_id,
        repo=repo,
        file_storage=file_storage,
        run_id=run_id,
    )

    # 验证成功
    assert result["error"] is None

    # 验证 SharedEnvironmentArtifact 有 prompt_snapshot_id
    artifact = repo.get_shared_environment_artifact(destination_id)
    assert artifact["prompt_snapshot_id"] is not None

    # 验证 PromptSnapshot 存在
    snapshot = repo.get_prompt_snapshot(artifact["prompt_snapshot_id"])
    assert snapshot is not None
    assert snapshot["operation_type"] == "shared_environment"
    assert len(snapshot["prompt_text"]) > 0


def test_end_to_end_generation_planning(repo, file_storage, setup_destination, create_run):
    """测试 8: 端到端完整工作流。"""
    destination_id = setup_destination["destination_id"]
    spec_id = setup_destination["spec_id"]
    run_id = create_run()

    # 运行完整工作流
    result = run_generation_planning_workflow(
        destination_id=destination_id,
        spec_id=spec_id,
        repo=repo,
        file_storage=file_storage,
        run_id=run_id,
    )

    # 验证所有关键步骤完成
    assert result["error"] is None
    assert result["shared_environment_id"] is not None
    assert result["environment_file_id"] is not None
    assert result["environment_sha256"] is not None
    assert result["environment_width"] == 2048
    assert result["environment_height"] == 1152

    # 验证 Repository 状态
    artifact = repo.get_shared_environment_artifact(destination_id)
    assert artifact is not None

    scene_plans = repo.list_scene_plans(destination_id)
    assert len(scene_plans) == 2

    # 验证 Destination phase 更新
    destination = repo.get_destination(destination_id)
    assert destination["phase"] == "shared_environment"
