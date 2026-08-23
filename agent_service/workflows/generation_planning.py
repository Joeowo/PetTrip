"""Generation Planning Workflow（T5 - Issue #17）。

从锁定的 DestinationSpec 到共享环境制品的生成链路。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, TypedDict

from ..adapters.image import ImageGenerationProvider, ImageGenerationRequest

from langgraph.graph import StateGraph, END

from ..storage.destination_storage import DestinationRepository
from ..storage.files import LocalImageStorage
from ..shared.ids import new_id


# ============================================================================
# Graph State
# ============================================================================


class GenerationPlanningState(TypedDict):
    """Generation Planning 工作流状态。"""

    # 输入
    destination_id: str
    spec_id: str

    # ScenePlans（从 Repository 读取）
    scene_plans: list[dict[str, Any]] | None

    # Prompt Snapshots
    prompt_snapshot_id: str | None

    # 共享环境生成
    shared_environment_id: str | None
    environment_file_id: str | None
    environment_sha256: str | None
    environment_width: int | None
    environment_height: int | None

    # 错误处理
    error: str | None
    attempt_number: int


# ============================================================================
# Mock 图像生成函数（T5 阶段使用 fixture，Issue #17 "快速主链路原则"）
# ============================================================================


def mock_generate_environment_image() -> bytes:
    """Mock 生成环境图片（真实实现会调用图片生成 Provider）。

    Returns:
        bytes: PNG 图片数据（2048x1152）
    """
    from PIL import Image
    from io import BytesIO

    # 创建一个简单的测试图片：渐变背景
    width, height = 2048, 1152
    image = Image.new("RGB", (width, height))
    pixels = image.load()

    for y in range(height):
        for x in range(width):
            # 简单的渐变效果
            r = int(135 + (x / width) * 50)
            g = int(206 + (y / height) * 30)
            b = int(235 - (x / width) * 50)
            pixels[x, y] = (r, g, b)

    # 转换为 PNG 字节
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ============================================================================
# Workflow Nodes
# ============================================================================


def create_scene_plans_node(
    state: GenerationPlanningState, repo: DestinationRepository
) -> GenerationPlanningState:
    """节点：创建场景计划（T3 已完成，这里只是读取）。"""
    destination_id = state["destination_id"]

    # 从 Repository 读取已创建的 ScenePlans
    scene_plans = repo.list_scene_plans(destination_id)

    if not scene_plans:
        state["error"] = "未找到场景计划"
        return state

    state["scene_plans"] = scene_plans
    return state


def validate_two_scene_invariants_node(
    state: GenerationPlanningState,
) -> GenerationPlanningState:
    """节点：验证两场景不变量。

    不变量（Issue #10 第 4.5 节）：
    1. 必须恰好 2 个 ScenePlan
    2. 两个场景必须具有不同的宠物行为或状态
    """
    scene_plans = state.get("scene_plans")

    if not scene_plans:
        state["error"] = "场景计划为空"
        return state

    # 不变量：必须恰好 2 个 ScenePlan
    if len(scene_plans) != 2:
        state["error"] = f"必须恰好 2 个场景计划，实际为 {len(scene_plans)}"
        return state

    # 不变量：两个场景的宠物行为或状态不能完全相同
    plan0 = scene_plans[0]
    plan1 = scene_plans[1]

    if (
        plan0["pet_behavior"] == plan1["pet_behavior"]
        and plan0["pet_emotion"] == plan1["pet_emotion"]
        and plan0["state_label"] == plan1["state_label"]
    ):
        state["error"] = "两个场景的宠物行为、情绪和状态标签不能完全相同"
        return state

    return state


def create_prompt_snapshots_node(
    state: GenerationPlanningState, repo: DestinationRepository
) -> GenerationPlanningState:
    """节点：创建 Prompt 快照（简化版）。"""
    destination_id = state["destination_id"]

    # 简化版：创建一个环境生成的 Prompt 快照
    # 真实实现会从 DestinationSpec 渲染完整 Prompt
    prompt_text = "生成温馨舒适的宠物旅行环境，包含两个语义明确的锚点位置"
    model_params = {
        "provider": "65535",
        "model": "gpt-image-2",
        "size": "16:9",
        "resolution": "2k",
        "quality": "high",
    }

    snapshot = repo.create_prompt_snapshot(
        destination_id=destination_id,
        operation_type="shared_environment",
        prompt_text=prompt_text,
        model_params=model_params,
    )

    state["prompt_snapshot_id"] = snapshot["snapshot_id"]
    return state


def generate_shared_environment_node(
    state: GenerationPlanningState,
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
    run_id: str,
    image_provider: ImageGenerationProvider | None = None,
) -> GenerationPlanningState:
    """节点：生成共享环境母图。

    算法（Issue #10 第 8.1 节）：
    1. 根据锁定 DestinationSpec 生成环境母图
    2. 使用 FileStorage 验证并原子写入 PNG
    3. 记录尺寸、MIME、SHA-256、PromptSnapshot
    4. 原子提交不可变 SharedEnvironmentArtifact
    """
    destination_id = state["destination_id"]
    prompt_snapshot_id = state.get("prompt_snapshot_id")
    attempt_number = state.get("attempt_number", 0)

    # 检查是否已经有环境母图（幂等性）
    existing = repo.get_shared_environment_artifact(destination_id)
    if existing:
        state["shared_environment_id"] = existing["shared_environment_id"]
        state["environment_file_id"] = existing["image_file_id"]
        state["environment_sha256"] = existing["image_sha256"]
        state["environment_width"] = existing["width_px"]
        state["environment_height"] = existing["height_px"]
        return state

    # 创建操作尝试记录
    attempt = repo.create_operation_attempt(
        destination_id=destination_id,
        scene_id=None,  # 环境生成没有 scene_id
        operation_type="shared_environment",
        attempt_number=attempt_number,
        run_id=run_id,
        status="started",
    )

    try:
        if image_provider is None:
            image_data = mock_generate_environment_image()
        else:
            result = asyncio.run(
                image_provider.generate(
                    ImageGenerationRequest(
                        prompt=(
                            "Create a 16:9 travel environment for a pet trip. "
                            "Keep the scene clear and suitable for placing a pet "
                            "in two distinct semantic locations."
                        )
                    )
                )
            )
            image_data = result.data

        # 使用 FileStorage 存储并验证
        file_id = new_id("file")
        stored_image = file_storage.normalize_and_store_generated(
            file_id=file_id,
            data=image_data,
            target_width=2048,
            target_height=1152,
            max_pixels=10_000_000,
        )

        # 在数据库中创建 file 记录
        from ..storage.database import Database
        db = Database(repo.db_path)

        # 获取 destination 的 api_client_id
        destination = repo.get_destination(destination_id)

        db.create_file(
            file_id=file_id,
            api_client_id=destination["api_client_id"],
            source="agent_generated",
            purpose="generated_image",
            mime_type=stored_image.mime_type,
            size_bytes=stored_image.size_bytes,
            width=stored_image.width,
            height=stored_image.height,
            rel_path=stored_image.rel_path,
            sha256=stored_image.sha256,
        )
        db.close()

        # 创建不可变的 SharedEnvironmentArtifact
        artifact = repo.create_shared_environment_artifact(
            destination_id=destination_id,
            source_run_id=run_id,
            image_file_id=file_id,
            image_sha256=stored_image.sha256,
            width_px=stored_image.width,
            height_px=stored_image.height,
            prompt_snapshot_id=prompt_snapshot_id,
        )

        # 更新尝试状态为成功
        repo.update_operation_attempt(
            attempt["attempt_id"],
            status="succeeded",
        )

        # 更新状态
        state["shared_environment_id"] = artifact["shared_environment_id"]
        state["environment_file_id"] = file_id
        state["environment_sha256"] = stored_image.sha256
        state["environment_width"] = stored_image.width
        state["environment_height"] = stored_image.height

        # 更新 Destination phase
        repo.update_destination_phase(destination_id, "shared_environment")

    except Exception as e:
        # 更新尝试状态为失败
        repo.update_operation_attempt(
            attempt["attempt_id"],
            status="failed",
            error_code="generation_failed",
            error_message=str(e),
        )

        # 检查是否还能重试（最多 3 次尝试：0, 1, 2）
        if attempt_number < 2:
            state["attempt_number"] = attempt_number + 1
            state["error"] = f"环境生成失败，准备第 {attempt_number + 2} 次尝试: {str(e)}"
        else:
            state["error"] = f"环境生成失败，已达最大重试次数: {str(e)}"

    return state


# ============================================================================
# Workflow Builder
# ============================================================================


def build_generation_planning_workflow(
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
    run_id: str,
    image_provider: ImageGenerationProvider | None = None,
) -> StateGraph:
    """构建 Generation Planning 工作流。

    Args:
        repo: DestinationRepository 实例
        file_storage: LocalImageStorage 实例
        run_id: Run ID（用于记录操作来源）

    Returns:
        StateGraph: 编译好的工作流图
    """
    workflow = StateGraph(GenerationPlanningState)

    # 添加节点
    workflow.add_node(
        "create_scene_plans",
        lambda state: create_scene_plans_node(state, repo),
    )
    workflow.add_node(
        "validate_two_scene_invariants",
        validate_two_scene_invariants_node,
    )
    workflow.add_node(
        "create_prompt_snapshots",
        lambda state: create_prompt_snapshots_node(state, repo),
    )
    workflow.add_node(
        "generate_shared_environment",
        lambda state: generate_shared_environment_node(
            state, repo, file_storage, run_id, image_provider=image_provider
        ),
    )

    # 设置入口点
    workflow.set_entry_point("create_scene_plans")

    # 添加边
    workflow.add_edge("create_scene_plans", "validate_two_scene_invariants")
    workflow.add_edge("validate_two_scene_invariants", "create_prompt_snapshots")
    workflow.add_edge("create_prompt_snapshots", "generate_shared_environment")
    workflow.add_edge("generate_shared_environment", END)

    return workflow.compile()


# ============================================================================
# 主入口函数
# ============================================================================


def run_generation_planning_workflow(
    destination_id: str,
    spec_id: str,
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
    run_id: str,
    image_provider: ImageGenerationProvider | None = None,
) -> dict[str, Any]:
    """运行 Generation Planning 工作流。

    Args:
        destination_id: 目的地 ID
        spec_id: 规格 ID
        repo: DestinationRepository 实例
        file_storage: LocalImageStorage 实例
        run_id: Run ID

    Returns:
        dict: 工作流最终状态，包含
            - shared_environment_id: str | None
            - environment_file_id: str | None
            - environment_sha256: str | None
            - environment_width: int | None
            - environment_height: int | None
            - error: str | None
    """
    # 构建工作流
    app = build_generation_planning_workflow(
        repo, file_storage, run_id, image_provider=image_provider
    )

    # 初始化状态
    initial_state: GenerationPlanningState = {
        "destination_id": destination_id,
        "spec_id": spec_id,
        "scene_plans": None,
        "prompt_snapshot_id": None,
        "shared_environment_id": None,
        "environment_file_id": None,
        "environment_sha256": None,
        "environment_width": None,
        "environment_height": None,
        "error": None,
        "attempt_number": 0,
    }

    # 运行工作流
    final_state = app.invoke(initial_state)

    return {
        "shared_environment_id": final_state.get("shared_environment_id"),
        "environment_file_id": final_state.get("environment_file_id"),
        "environment_sha256": final_state.get("environment_sha256"),
        "environment_width": final_state.get("environment_width"),
        "environment_height": final_state.get("environment_height"),
        "error": final_state.get("error"),
    }
