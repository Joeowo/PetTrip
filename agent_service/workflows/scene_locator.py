"""Scene Locator Workflow（T6 - Issue #18）。

场景定位与圆检测工作流：
1. 生成定位参考图（locator image）
2. 检测黑圈圆心坐标
3. 最多 3 次重试机制
4. 禁止兜底
"""

from __future__ import annotations

import hashlib
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END

from ..storage.destination_storage import DestinationRepository
from ..storage.files import LocalImageStorage
from ..shared.ids import new_id
from ..domain.interaction_circle import (
    detect_black_circle,
    validate_circle_in_bounds,
    DetectionError,
)


# ============================================================================
# Graph State
# ============================================================================


class SceneLocatorState(TypedDict):
    """Scene Locator 工作流状态。"""

    # 输入
    destination_id: str
    scene_id: str
    shared_environment_id: str
    environment_file_id: str
    environment_sha256: str
    environment_width: int
    environment_height: int
    semantic_anchor: str

    # 定位参考图生成
    locator_file_id: str | None
    locator_sha256: str | None
    locator_attempt_number: int

    # 圆心检测
    planned_center_x: int | None
    planned_center_y: int | None
    detection_diagnostics: dict[str, Any] | None

    # 配置
    interaction_diameter_px: int  # 固定直径（偶数）

    # 错误处理
    error: str | None
    max_attempts: int


# ============================================================================
# Mock 定位参考图生成（T6 简化，Issue #18 "快速主链路原则"）
# ============================================================================


def mock_generate_locator_image(
    environment_image_bytes: bytes,
    semantic_anchor: str,
    width: int,
    height: int,
) -> bytes:
    """Mock 生成定位参考图（真实实现会调用图片生成 Provider）。

    当前简化实现：在环境母图上绘制一个黑圈作为测试用定位标记。

    Args:
        environment_image_bytes: 环境母图 PNG 字节
        semantic_anchor: 语义锚点描述
        width: 图像宽度
        height: 图像高度

    Returns:
        bytes: 定位参考图 PNG 字节
    """
    from PIL import Image, ImageDraw
    from io import BytesIO

    # 加载环境母图
    env_image = Image.open(BytesIO(environment_image_bytes)).convert("RGB")

    # 在中心偏移位置绘制黑色圆（模拟 Provider 生成的定位标记）
    # 实际生产中，这里会调用图片生成 Provider 根据 semantic_anchor 生成带黑圈的图
    draw = ImageDraw.Draw(env_image)

    # 简单策略：根据 semantic_anchor 哈希值选择位置（确保可复现）
    anchor_hash = hash(semantic_anchor)
    offset_x = (anchor_hash % 400) - 200  # -200 to 200
    offset_y = ((anchor_hash // 1000) % 200) - 100  # -100 to 100

    center_x = width // 2 + offset_x
    center_y = height // 2 + offset_y
    radius = 80

    left = center_x - radius
    top = center_y - radius
    right = center_x + radius
    bottom = center_y + radius

    draw.ellipse([left, top, right, bottom], fill=(0, 0, 0))

    # 转换为 PNG 字节
    buffer = BytesIO()
    env_image.save(buffer, format="PNG")
    return buffer.getvalue()


# ============================================================================
# Workflow Nodes
# ============================================================================


def generate_locator_node(
    state: SceneLocatorState,
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
) -> SceneLocatorState:
    """节点：生成定位参考图。

    当前使用 mock 函数简化实现（Issue #18 快速主链路原则）。
    """
    try:
        # 读取环境母图
        env_file = file_storage.read(state["environment_file_id"])
        env_bytes = env_file.content

        # 生成定位参考图（mock）
        locator_bytes = mock_generate_locator_image(
            env_bytes,
            state["semantic_anchor"],
            state["environment_width"],
            state["environment_height"],
        )

        # 存储定位参考图
        locator_sha256 = hashlib.sha256(locator_bytes).hexdigest()
        locator_file_id = new_id()

        file_storage.write(
            file_id=locator_file_id,
            content=locator_bytes,
            mime_type="image/png",
        )

        # 记录到 Repository（内部资产，不直接交付 Unity）
        # TODO: 添加 locator_artifacts 表来追溯定位参考图

        state["locator_file_id"] = locator_file_id
        state["locator_sha256"] = locator_sha256
        state["locator_attempt_number"] = state.get("locator_attempt_number", 0)

    except Exception as e:
        state["error"] = f"locator_generation_failed: {str(e)}"

    return state


def detect_circle_center_node(
    state: SceneLocatorState,
    file_storage: LocalImageStorage,
) -> SceneLocatorState:
    """节点：检测黑圈圆心坐标。"""
    try:
        # 读取环境母图和定位参考图
        env_file = file_storage.read(state["environment_file_id"])
        loc_file = file_storage.read(state["locator_file_id"])

        env_bytes = env_file.content
        loc_bytes = loc_file.content

        # 调用黑圈检测算法
        detection_result = detect_black_circle(env_bytes, loc_bytes)

        # 提取圆心坐标
        center_x, center_y = detection_result["planned_locator_center"]

        # 验证圆心 + 配置半径是否在画布内
        radius_px = state["interaction_diameter_px"] // 2
        is_valid = validate_circle_in_bounds(
            center_x,
            center_y,
            radius_px,
            state["environment_width"],
            state["environment_height"],
        )

        if not is_valid:
            state["error"] = "circle_out_of_bounds"
            state["detection_diagnostics"] = {
                "center": (center_x, center_y),
                "radius": radius_px,
                "canvas": (state["environment_width"], state["environment_height"]),
            }
        else:
            # 检测成功
            state["planned_center_x"] = center_x
            state["planned_center_y"] = center_y
            state["detection_diagnostics"] = detection_result

    except DetectionError as e:
        # 检测失败（无圆、多圆等）
        state["error"] = f"detection_failed: {e.reason}"
        state["detection_diagnostics"] = {
            "reason": e.reason,
            "rejection_counts": e.rejection_counts,
        }

    except Exception as e:
        state["error"] = f"detection_error: {str(e)}"

    return state


def should_retry_node(state: SceneLocatorState) -> str:
    """决策节点：是否重试定位。

    规则（Issue #18）：
    - 最多 3 attempts（attempt 0, 1, 2）
    - 检测成功 → 结束
    - 检测失败且未达上限 → 重试
    - 检测失败且达上限 → 最终失败
    """
    if state["error"] is None:
        # 成功
        return "success"

    current_attempt = state.get("locator_attempt_number", 0)

    if current_attempt < state["max_attempts"] - 1:
        # 还有重试机会
        return "retry"
    else:
        # 重试耗尽
        return "failed"


def retry_locator_node(state: SceneLocatorState) -> SceneLocatorState:
    """节点：准备重试。"""
    state["locator_attempt_number"] = state.get("locator_attempt_number", 0) + 1
    state["error"] = None
    state["locator_file_id"] = None
    state["locator_sha256"] = None
    return state


# ============================================================================
# Workflow Builder
# ============================================================================


def build_scene_locator_workflow(
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
) -> StateGraph:
    """构建场景定位工作流。

    流程：
    1. generate_locator → 生成定位参考图
    2. detect_circle_center → 检测黑圈圆心
    3. should_retry → 决策：成功 | 重试 | 失败
    4. retry_locator → 重试（回到步骤 1）
    """
    workflow = StateGraph(SceneLocatorState)

    # 添加节点
    workflow.add_node(
        "generate_locator",
        lambda state: generate_locator_node(state, repo, file_storage),
    )
    workflow.add_node(
        "detect_circle_center",
        lambda state: detect_circle_center_node(state, file_storage),
    )
    workflow.add_node("retry_locator", retry_locator_node)

    # 设置入口
    workflow.set_entry_point("generate_locator")

    # 连接节点
    workflow.add_edge("generate_locator", "detect_circle_center")

    # 决策分支
    workflow.add_conditional_edges(
        "detect_circle_center",
        should_retry_node,
        {
            "success": END,
            "retry": "retry_locator",
            "failed": END,
        },
    )

    workflow.add_edge("retry_locator", "generate_locator")

    return workflow


# ============================================================================
# 运行接口
# ============================================================================


def run_scene_locator_workflow(
    destination_id: str,
    scene_id: str,
    shared_environment_id: str,
    semantic_anchor: str,
    interaction_diameter_px: int,
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
) -> dict[str, Any]:
    """运行场景定位工作流。

    Args:
        destination_id: 目的地 ID
        scene_id: 场景 ID
        shared_environment_id: 共享环境 ID
        semantic_anchor: 语义锚点描述
        interaction_diameter_px: 交互圆直径（偶数）
        repo: Repository
        file_storage: 文件存储

    Returns:
        最终状态字典，包含圆心坐标或错误信息
    """
    # 读取共享环境制品
    env_artifact = repo.get_shared_environment_artifact(shared_environment_id)

    # 构建初始状态
    initial_state = SceneLocatorState(
        destination_id=destination_id,
        scene_id=scene_id,
        shared_environment_id=shared_environment_id,
        environment_file_id=env_artifact["image_file_id"],
        environment_sha256=env_artifact["image_sha256"],
        environment_width=env_artifact["width_px"],
        environment_height=env_artifact["height_px"],
        semantic_anchor=semantic_anchor,
        locator_file_id=None,
        locator_sha256=None,
        locator_attempt_number=0,
        planned_center_x=None,
        planned_center_y=None,
        detection_diagnostics=None,
        interaction_diameter_px=interaction_diameter_px,
        error=None,
        max_attempts=3,  # Issue #18: 最多 3 attempts
    )

    # 构建并运行工作流
    workflow = build_scene_locator_workflow(repo, file_storage)
    app = workflow.compile()

    final_state = app.invoke(initial_state)

    return final_state
