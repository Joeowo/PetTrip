"""Scene Generation Workflow（T7 - Issue #19）。

场景最终生成工作流：
1. 确保共享环境存在（ensure_shared_environment）
2. 生成定位参考图（generate_localization_reference）
3. 检测交互圆（detect_interaction_circle）
4. 构建生成 Mask（build_generation_mask）
5. 生成最终场景（generate_final_scene）
6. 验证场景制品（validate_scene_artifact）
7. 提交场景制品（commit_scene_artifact）

最终场景生成最多 3 次重试，保持 Spec、Plan、环境母图、Mask、圆不变。
"""

from __future__ import annotations

import hashlib
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END

from ..storage.destination_storage import DestinationRepository
from ..storage.files import LocalImageStorage
from ..shared.ids import new_id
from ..domain.mask_generation import generate_mask_and_aperture
from ..domain.interaction_circle import validate_circle_in_bounds


# ============================================================================
# Graph State
# ============================================================================


class SceneGenerationState(TypedDict):
    """Scene Generation 工作流状态。"""

    # 输入
    destination_id: str
    scene_id: str
    spec_id: str
    shared_environment_id: str
    semantic_anchor: str
    pet_behavior: str
    pet_emotion: str

    # 共享环境信息
    environment_file_id: str
    environment_sha256: str
    environment_width: int
    environment_height: int

    # 定位与圆检测（来自 T6）
    planned_center_x: int | None
    planned_center_y: int | None
    interaction_diameter_px: int

    # Mask 生成
    generation_mask_file_id: str | None
    generation_mask_sha256: str | None
    aperture_file_id: str | None
    aperture_sha256: str | None

    # 最终场景生成
    final_scene_file_id: str | None
    final_scene_sha256: str | None
    final_scene_width: int | None
    final_scene_height: int | None
    scene_generation_attempt: int

    # InteractionZone
    interaction_zone_id: str | None

    # SceneArtifact
    scene_artifact_id: str | None
    artifact_ready: bool

    # Prompt Snapshot
    prompt_snapshot_id: str | None

    # 错误处理
    error: str | None
    max_scene_attempts: int

    # 配置
    use_mock_final_scene: bool  # 是否使用 fixture PNG


# ============================================================================
# Mock 最终场景生成（T7 简化，Issue #19 "快速主链路原则"）
# ============================================================================


def mock_generate_final_scene(
    aperture_image_bytes: bytes,
    pet_behavior: str,
    pet_emotion: str,
    width: int,
    height: int,
) -> bytes:
    """Mock 生成最终场景（真实实现会调用图片生成 Provider）。

    当前简化实现：在 aperture 图上绘制简单的宠物占位符。

    Args:
        aperture_image_bytes: 打洞参考图 PNG 字节
        pet_behavior: 宠物行为描述
        pet_emotion: 宠物情绪描述
        width: 图像宽度
        height: 图像高度

    Returns:
        bytes: 最终场景 PNG 字节
    """
    from PIL import Image, ImageDraw
    from io import BytesIO

    # 加载 aperture 图
    aperture_image = Image.open(BytesIO(aperture_image_bytes)).convert("RGB")

    # 在黑色圆的位置绘制简单的宠物占位符（绿色圆 + 文字）
    draw = ImageDraw.Draw(aperture_image)

    # 找到黑色圆的中心（简单策略：扫描图像找黑色区域）
    # 这里为了简化，直接使用图像中心附近
    center_x = width // 2
    center_y = height // 2
    pet_radius = 60

    # 绘制绿色圆（模拟宠物）
    left = center_x - pet_radius
    top = center_y - pet_radius
    right = center_x + pet_radius
    bottom = center_y + pet_radius
    draw.ellipse([left, top, right, bottom], fill=(50, 200, 50))

    # 添加文字标注（可选）
    # draw.text((center_x, center_y), "PET", fill=(255, 255, 255))

    # 转换为 PNG 字节
    buffer = BytesIO()
    aperture_image.save(buffer, format="PNG")
    return buffer.getvalue()


# ============================================================================
# Workflow Nodes
# ============================================================================


def ensure_shared_environment_node(
    state: SceneGenerationState,
    repo: DestinationRepository,
) -> SceneGenerationState:
    """节点：确保共享环境存在。"""
    try:
        env_artifact = repo.get_shared_environment_artifact_by_id(
            state["shared_environment_id"]
        )

        if env_artifact is None:
            state["error"] = "shared_environment_not_found"
            return state

        # 填充环境信息
        state["environment_file_id"] = env_artifact["image_file_id"]
        state["environment_sha256"] = env_artifact["image_sha256"]
        state["environment_width"] = env_artifact["width_px"]
        state["environment_height"] = env_artifact["height_px"]

    except Exception as e:
        state["error"] = f"shared_environment_error: {str(e)}"

    return state


def generate_localization_reference_node(
    state: SceneGenerationState,
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
) -> SceneGenerationState:
    """节点：生成定位参考图（调用 T6 Scene Locator）。

    注意：此节点在 T7 中可以省略，因为 T6 已经运行过。
    这里保留接口完整性，实际可以直接从数据库读取 T6 的结果。
    """
    # T7 假设 T6 已经执行，planned_center_x/y 已在输入中提供
    # 此节点为空操作（no-op）
    return state


def detect_interaction_circle_node(
    state: SceneGenerationState,
) -> SceneGenerationState:
    """节点：检测交互圆（验证圆心是否有效）。

    注意：T6 已经完成圆心检测，此节点只做验证。
    """
    try:
        if state["planned_center_x"] is None or state["planned_center_y"] is None:
            state["error"] = "missing_planned_center"
            return state

        # 验证圆心 + 半径是否在画布内
        radius_px = state["interaction_diameter_px"] // 2
        is_valid = validate_circle_in_bounds(
            state["planned_center_x"],
            state["planned_center_y"],
            radius_px,
            state["environment_width"],
            state["environment_height"],
        )

        if not is_valid:
            state["error"] = "circle_out_of_bounds"

    except Exception as e:
        state["error"] = f"circle_validation_error: {str(e)}"

    return state


def build_generation_mask_node(
    state: SceneGenerationState,
    file_storage: LocalImageStorage,
) -> SceneGenerationState:
    """节点：构建生成 Mask（字节稳定）。"""
    try:
        # 读取环境母图
        env_file = file_storage.read(state["environment_file_id"])
        env_bytes = env_file.content

        # 生成 Mask 和 aperture
        mask_result = generate_mask_and_aperture(
            env_bytes,
            state["planned_center_x"],
            state["planned_center_y"],
            state["interaction_diameter_px"],
        )

        # 存储 generation mask（内部资产）
        mask_file_id = new_id("mask")
        file_storage.write(
            file_id=mask_file_id,
            content=mask_result["generation_mask_bytes"],
            mime_type="image/png",
        )

        # 存储 aperture image（内部资产）
        aperture_file_id = new_id("aperture")
        file_storage.write(
            file_id=aperture_file_id,
            content=mask_result["aperture_image_bytes"],
            mime_type="image/png",
        )

        # 更新状态
        state["generation_mask_file_id"] = mask_file_id
        state["generation_mask_sha256"] = mask_result["generation_mask_sha256"]
        state["aperture_file_id"] = aperture_file_id
        state["aperture_sha256"] = mask_result["aperture_image_sha256"]

    except Exception as e:
        state["error"] = f"mask_generation_failed: {str(e)}"

    return state


def generate_final_scene_node(
    state: SceneGenerationState,
    file_storage: LocalImageStorage,
) -> SceneGenerationState:
    """节点：生成最终场景。"""
    # 如果已经有错误，跳过生成
    if state.get("error") is not None:
        return state

    try:
        # 读取 aperture 图
        aperture_file = file_storage.read(state["aperture_file_id"])
        aperture_bytes = aperture_file.content

        # 生成最终场景（mock 或真实 Provider）
        if state["use_mock_final_scene"]:
            final_scene_bytes = mock_generate_final_scene(
                aperture_bytes,
                state["pet_behavior"],
                state["pet_emotion"],
                state["environment_width"],
                state["environment_height"],
            )
        else:
            # TODO: 调用真实图片生成 Provider
            # final_scene_bytes = call_image_generation_provider(...)
            raise NotImplementedError("真实 Provider 调用尚未实现")

        # 计算哈希
        final_scene_sha256 = hashlib.sha256(final_scene_bytes).hexdigest()

        # 存储最终场景
        final_scene_file_id = new_id("scene")
        file_storage.write(
            file_id=final_scene_file_id,
            content=final_scene_bytes,
            mime_type="image/png",
        )

        # 更新状态
        state["final_scene_file_id"] = final_scene_file_id
        state["final_scene_sha256"] = final_scene_sha256
        state["final_scene_width"] = state["environment_width"]
        state["final_scene_height"] = state["environment_height"]

    except Exception as e:
        state["error"] = f"final_scene_generation_failed: {str(e)}"

    return state


def validate_scene_artifact_node(
    state: SceneGenerationState,
) -> SceneGenerationState:
    """节点：验证场景制品（格式、尺寸、哈希）。

    简化版验证：
    - 最终场景文件存在
    - 尺寸匹配环境母图
    - 哈希已计算
    """
    # 如果已经有错误，跳过验证
    if state.get("error") is not None:
        return state

    try:
        if state["final_scene_file_id"] is None:
            state["error"] = "missing_final_scene"
            return state

        if state["final_scene_sha256"] is None:
            state["error"] = "missing_scene_hash"
            return state

        # 尺寸验证
        if state["final_scene_width"] != state["environment_width"]:
            state["error"] = "scene_width_mismatch"
            return state

        if state["final_scene_height"] != state["environment_height"]:
            state["error"] = "scene_height_mismatch"
            return state

        # 验证通过
        # （更复杂的验证：宠物是否出现、环境是否保持等，留给后续阶段）

    except Exception as e:
        state["error"] = f"validation_error: {str(e)}"

    return state


def commit_scene_artifact_node(
    state: SceneGenerationState,
    repo: DestinationRepository,
    storage=None,
) -> SceneGenerationState:
    """节点：原子提交场景制品。

    原子性保证：
    1. 创建 InteractionZone
    2. 创建 SceneArtifact（引用 InteractionZone、render asset、环境哈希）
    3. 所有操作在同一事务中完成

    Args:
        state: 工作流状态
        repo: Repository
        storage: Storage 实例（可选，用于注册文件到 files 表）
    """
    try:
        # 如果提供了 storage，注册最终场景文件到 files 表
        if storage is not None and state["final_scene_file_id"] is not None:
            # 获取 api_client_id（从 destination 获取）
            destination = repo.get_destination(state["destination_id"])
            if destination:
                storage.create_file(
                    file_id=state["final_scene_file_id"],
                    api_client_id=destination["api_client_id"],
                    source="agent_generated",
                    purpose="generated_image",
                    mime_type="image/png",
                    size_bytes=0,  # 简化：实际应该从文件获取
                    sha256=state["final_scene_sha256"],
                    width=state["final_scene_width"],
                    height=state["final_scene_height"],
                    rel_path=f"{state['final_scene_file_id']}.dat",
                )

        with repo.transaction() as conn:
            # 1. 创建 InteractionZone
            zone_id = new_id("zone")
            radius_px = state["interaction_diameter_px"] // 2

            conn.execute(
                """
                INSERT INTO interaction_zones(
                    zone_id, coordinate_space, canvas_width_px, canvas_height_px,
                    shape, center_x_px, center_y_px, radius_px, created_at
                )
                VALUES(?, 'pixel_top_left', ?, ?, 'circle', ?, ?, ?, datetime('now'))
                """,
                (
                    zone_id,
                    state["environment_width"],
                    state["environment_height"],
                    state["planned_center_x"],
                    state["planned_center_y"],
                    radius_px,
                ),
            )

            # 2. 创建 SceneArtifact
            artifact_id = new_id("artifact")

            conn.execute(
                """
                INSERT INTO scene_artifacts(
                    scene_artifact_id, scene_id, destination_id, artifact_version,
                    render_file_id, render_mime_type, render_width_px, render_height_px,
                    render_sha256, interaction_zone_id, shared_environment_sha256,
                    prompt_snapshot_id, created_at
                )
                VALUES(?, ?, ?, 1, ?, 'image/png', ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    artifact_id,
                    state["scene_id"],
                    state["destination_id"],
                    state["final_scene_file_id"],
                    state["final_scene_width"],
                    state["final_scene_height"],
                    state["final_scene_sha256"],
                    zone_id,
                    state["environment_sha256"],
                    state.get("prompt_snapshot_id"),
                ),
            )

            # 事务提交（自动）
            state["interaction_zone_id"] = zone_id
            state["scene_artifact_id"] = artifact_id
            state["artifact_ready"] = True

    except Exception as e:
        state["error"] = f"commit_failed: {str(e)}"

    return state


# ============================================================================
# 决策节点
# ============================================================================


def should_retry_scene_generation(state: SceneGenerationState) -> str:
    """决策节点：是否重试最终场景生成。

    规则（Issue #19）：
    - 最多 3 attempts（attempt 0, 1, 2）
    - 验证成功 → 提交
    - 验证失败且未达上限 → 重试
    - 验证失败且达上限 → 最终失败
    """
    # 如果没有错误，继续提交
    if state["error"] is None:
        return "commit"

    # 检查是否是最终场景生成失败（可重试的错误）
    error = state["error"]
    retryable_errors = [
        "final_scene_generation_failed",
        "validation_error",
        "scene_width_mismatch",
        "scene_height_mismatch",
    ]

    is_retryable = any(err in error for err in retryable_errors)

    if not is_retryable:
        # 不可重试的错误（如 shared_environment_not_found）
        return "failed"

    current_attempt = state.get("scene_generation_attempt", 0)

    if current_attempt < state["max_scene_attempts"] - 1:
        # 还有重试机会
        return "retry"
    else:
        # 重试耗尽
        return "failed"


def retry_final_scene_node(state: SceneGenerationState) -> SceneGenerationState:
    """节点：准备重试最终场景生成。

    重试时保持不变：
    - Spec、ScenePlan
    - 环境母图
    - Mask（generation_mask、aperture）
    - 圆心和半径
    - PromptSnapshot
    """
    state["scene_generation_attempt"] = state.get("scene_generation_attempt", 0) + 1
    state["error"] = None
    state["final_scene_file_id"] = None
    state["final_scene_sha256"] = None
    state["final_scene_width"] = None
    state["final_scene_height"] = None
    return state


# ============================================================================
# Workflow Builder
# ============================================================================


def build_scene_generation_workflow(
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
    storage=None,
) -> StateGraph:
    """构建场景生成工作流。

    流程：
    1. ensure_shared_environment → 确保共享环境存在
    2. generate_localization_reference → 生成定位参考图（T6 已完成，此处为 no-op）
    3. detect_interaction_circle → 验证圆心
    4. build_generation_mask → 生成 Mask 和 aperture
    5. generate_final_scene → 生成最终场景
    6. validate_scene_artifact → 验证场景制品
    7. should_retry → 决策：提交 | 重试 | 失败
    8. commit_scene_artifact → 原子提交

    Args:
        repo: Repository
        file_storage: 文件存储
        storage: Storage 实例（可选，用于注册文件）
    """
    workflow = StateGraph(SceneGenerationState)

    # 添加节点
    workflow.add_node(
        "ensure_shared_environment",
        lambda state: ensure_shared_environment_node(state, repo),
    )
    workflow.add_node(
        "generate_localization_reference",
        lambda state: generate_localization_reference_node(state, repo, file_storage),
    )
    workflow.add_node(
        "detect_interaction_circle",
        lambda state: detect_interaction_circle_node(state),
    )
    workflow.add_node(
        "build_generation_mask",
        lambda state: build_generation_mask_node(state, file_storage),
    )
    workflow.add_node(
        "generate_final_scene",
        lambda state: generate_final_scene_node(state, file_storage),
    )
    workflow.add_node(
        "validate_scene_artifact",
        lambda state: validate_scene_artifact_node(state),
    )
    workflow.add_node(
        "commit_scene_artifact",
        lambda state: commit_scene_artifact_node(state, repo, storage),
    )
    workflow.add_node("retry_final_scene", retry_final_scene_node)

    # 设置入口
    workflow.set_entry_point("ensure_shared_environment")

    # 连接节点（线性流程，直到重试决策点）
    workflow.add_edge("ensure_shared_environment", "generate_localization_reference")
    workflow.add_edge("generate_localization_reference", "detect_interaction_circle")
    workflow.add_edge("detect_interaction_circle", "build_generation_mask")
    workflow.add_edge("build_generation_mask", "generate_final_scene")
    workflow.add_edge("generate_final_scene", "validate_scene_artifact")

    # 决策分支
    workflow.add_conditional_edges(
        "validate_scene_artifact",
        should_retry_scene_generation,
        {
            "commit": "commit_scene_artifact",
            "retry": "retry_final_scene",
            "failed": END,
        },
    )

    # 重试回到最终场景生成
    workflow.add_edge("retry_final_scene", "generate_final_scene")

    # 提交后结束
    workflow.add_edge("commit_scene_artifact", END)

    return workflow


# ============================================================================
# 运行接口
# ============================================================================


def run_scene_generation_workflow(
    destination_id: str,
    scene_id: str,
    spec_id: str,
    shared_environment_id: str,
    semantic_anchor: str,
    pet_behavior: str,
    pet_emotion: str,
    planned_center_x: int,
    planned_center_y: int,
    interaction_diameter_px: int,
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
    use_mock_final_scene: bool = True,
    storage=None,
) -> dict[str, Any]:
    """运行场景生成工作流。

    Args:
        destination_id: 目的地 ID
        scene_id: 场景 ID
        spec_id: 规格 ID
        shared_environment_id: 共享环境 ID
        semantic_anchor: 语义锚点描述
        pet_behavior: 宠物行为
        pet_emotion: 宠物情绪
        planned_center_x: 圆心 X（来自 T6）
        planned_center_y: 圆心 Y（来自 T6）
        interaction_diameter_px: 交互圆直径
        repo: Repository
        file_storage: 文件存储
        use_mock_final_scene: 是否使用 mock 最终场景（默认 True）
        storage: Storage 实例（可选，用于注册文件）

    Returns:
        最终状态字典，包含 SceneArtifact ID 或错误信息
    """
    # 构建初始状态
    initial_state = SceneGenerationState(
        destination_id=destination_id,
        scene_id=scene_id,
        spec_id=spec_id,
        shared_environment_id=shared_environment_id,
        semantic_anchor=semantic_anchor,
        pet_behavior=pet_behavior,
        pet_emotion=pet_emotion,
        environment_file_id="",  # 由 ensure_shared_environment 填充
        environment_sha256="",
        environment_width=0,
        environment_height=0,
        planned_center_x=planned_center_x,
        planned_center_y=planned_center_y,
        interaction_diameter_px=interaction_diameter_px,
        generation_mask_file_id=None,
        generation_mask_sha256=None,
        aperture_file_id=None,
        aperture_sha256=None,
        final_scene_file_id=None,
        final_scene_sha256=None,
        final_scene_width=None,
        final_scene_height=None,
        scene_generation_attempt=0,
        interaction_zone_id=None,
        scene_artifact_id=None,
        artifact_ready=False,
        prompt_snapshot_id=None,
        error=None,
        max_scene_attempts=3,  # Issue #19: 最多 3 attempts
        use_mock_final_scene=use_mock_final_scene,
    )

    # 构建并运行工作流
    workflow = build_scene_generation_workflow(repo, file_storage, storage)
    app = workflow.compile()

    final_state = app.invoke(initial_state)

    return final_state
