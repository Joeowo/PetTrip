"""Scene Locator Workflow（T6 - Issue #18）。

场景定位与圆检测工作流：
1. 生成定位参考图（locator image）
2. 检测黑圈圆心坐标
3. 最多 3 次重试机制
4. 禁止兜底
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any, TypedDict

from PIL import Image

from langgraph.graph import StateGraph, END

from ..storage.destination_storage import DestinationRepository
from ..storage.files import LocalImageStorage
from ..shared.ids import new_id
from ..domain.interaction_circle import (
    detect_black_circle,
    validate_circle_in_bounds,
    DetectionError,
)
from ..adapters.llm import ChatMessage, ChatModelProvider, VisionImage
from ..shared.structured_output import StructuredOutputRegistry


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
    visual_anchor: dict[str, str] | None

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


def draw_locator_image(
    environment_image_bytes: bytes,
    center_x: int,
    center_y: int,
    diameter_px: int,
) -> bytes:
    """在原始环境图副本上确定性绘制精确尺寸的纯黑实心圆。"""
    from PIL import ImageDraw

    env_image = Image.open(BytesIO(environment_image_bytes)).convert("RGB")
    radius = diameter_px // 2
    draw = ImageDraw.Draw(env_image)
    draw.ellipse(
        [center_x - radius, center_y - radius, center_x + radius, center_y + radius],
        fill=(0, 0, 0),
    )
    buffer = BytesIO()
    env_image.save(buffer, format="PNG")
    return buffer.getvalue()


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

    # fixture 使用稳定摘要派生位置，确保跨进程重启仍可复现。
    anchor_hash = int.from_bytes(
        hashlib.sha256(semantic_anchor.encode("utf-8")).digest()[:8], "big"
    )
    offset_x = (anchor_hash % 400) - 200  # -200 to 199
    offset_y = ((anchor_hash // 1000) % 200) - 100  # -100 to 99

    center_x = width // 2 + offset_x
    center_y = height // 2 + offset_y
    return draw_locator_image(
        environment_image_bytes,
        center_x,
        center_y,
        diameter_px=160,
    )


# ============================================================================
# Workflow Nodes
# ============================================================================


def _read_registered_bytes(
    file_storage: LocalImageStorage,
    repo: DestinationRepository,
    file_id: str,
    destination_id: str,
) -> bytes:
    if hasattr(file_storage, "read"):
        return file_storage.read(file_id).content
    destination = repo.get_destination(destination_id)
    if destination is None:
        raise FileNotFoundError(destination_id)
    from ..storage.database import Database

    db = Database(repo.db_path)
    try:
        file_row = db.get_file(file_id, destination["api_client_id"])
    finally:
        db.close()
    if file_row is None:
        raise FileNotFoundError(file_id)
    return file_storage.read_verified(file_row)


def _store_locator_bytes(
    file_storage: LocalImageStorage,
    repo: DestinationRepository,
    destination_id: str,
    file_id: str,
    content: bytes,
) -> dict[str, Any]:
    with Image.open(BytesIO(content)) as image:
        image.load()
        width, height = image.size
    if hasattr(file_storage, "write"):
        file_storage.write(file_id, content, "image/png")
        rel_path = f"{file_id}.dat"
    else:
        stored = file_storage.store_generated_png(
            file_id=file_id,
            data=content,
            width=width,
            height=height,
        )
        rel_path = stored.rel_path
    from ..storage.database import Database

    destination = repo.get_destination(destination_id)
    if destination is None:
        raise FileNotFoundError(destination_id)
    db = Database(repo.db_path)
    try:
        db.create_file(
            file_id=file_id,
            api_client_id=destination["api_client_id"],
            source="agent_generated",
            purpose="generated_image",
            mime_type="image/png",
            size_bytes=len(content),
            width=width,
            height=height,
            rel_path=rel_path,
            sha256=hashlib.sha256(content).hexdigest(),
        )
    except Exception:
        if not hasattr(file_storage, "write"):
            file_storage.delete(rel_path)
        raise
    finally:
        db.close()
    return {"width": width, "height": height, "rel_path": rel_path}


def generate_locator_node(
    state: SceneLocatorState,
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
    vision_provider: ChatModelProvider | None = None,
) -> SceneLocatorState:
    """节点：生成定位参考图。

    当前使用 mock 函数简化实现（Issue #18 快速主链路原则）。
    """
    try:
        # 读取环境母图
        env_bytes = _read_registered_bytes(
            file_storage,
            repo,
            state["environment_file_id"],
            state["destination_id"],
        )

        if vision_provider is None:
            locator_bytes = mock_generate_locator_image(
                env_bytes,
                state["semantic_anchor"],
                state["environment_width"],
                state["environment_height"],
            )
        else:
            registry = StructuredOutputRegistry()
            request = registry.request_for(
                schema_name="locator_selection",
                schema_version="1.0",
            )
            anchor = state.get("visual_anchor") or {}
            anchor_context = (
                f"Visual anchor label: {anchor.get('label', '')}. "
                f"Landmark: {anchor.get('landmark', '')}. "
                f"Interaction affordance: {anchor.get('interaction_affordance', '')}. "
                f"Placement guidance: {anchor.get('placement_guidance', '')}. "
                f"Pet activity: {anchor.get('pet_activity', '')}. "
            )
            vision_prompt = (
                "Use the attached environment image as the only visual source of truth. "
                "Do not generate or edit an image. Select one integer pixel center where "
                "the pet can be placed for this semantic anchor. "
                f"Scene semantic anchor: {state['semantic_anchor']}. {anchor_context}"
                "Choose a visible, walkable or restable area; "
                "avoid sky, water, lighthouse walls, buildings, existing animals, and canvas "
                f"edges. The image dimensions are exactly {state['environment_width']}x"
                f"{state['environment_height']}. Return only the locator_selection JSON."
            )
            raw = __import__("asyncio").run(
                vision_provider.complete_structured(
                    [
                        ChatMessage(
                            role="user",
                            content=vision_prompt,
                            images=(
                                VisionImage(mime_type="image/png", data=env_bytes),
                            ),
                        )
                    ],
                    request,
                )
            )
            selection = registry.parse_and_validate(
                raw,
                schema_name="locator_selection",
                schema_version="1.0",
            )
            center_x = selection["center_x"]
            center_y = selection["center_y"]
            radius = state["interaction_diameter_px"] // 2
            if not validate_circle_in_bounds(
                center_x,
                center_y,
                radius,
                state["environment_width"],
                state["environment_height"],
            ):
                state["error"] = "locator_center_out_of_bounds"
                state["detection_diagnostics"] = selection
                return state
            locator_bytes = draw_locator_image(
                env_bytes,
                center_x,
                center_y,
                state["interaction_diameter_px"],
            )
            state["detection_diagnostics"] = {
                "source": "chat_vision_locator_selection",
                "selection": selection,
            }

        # 存储定位参考图
        locator_sha256 = hashlib.sha256(locator_bytes).hexdigest()
        locator_file_id = new_id("locator")

        _store_locator_bytes(
            file_storage,
            repo,
            state["destination_id"],
            locator_file_id,
            locator_bytes,
        )

        state["locator_file_id"] = locator_file_id
        state["locator_sha256"] = locator_sha256
        state["locator_attempt_number"] = state.get("locator_attempt_number", 0)

    except Exception as e:
        state["error"] = f"locator_generation_failed: {str(e)}"

    return state


def detect_circle_center_node(
    state: SceneLocatorState,
    repo: DestinationRepository,
    file_storage: LocalImageStorage,
) -> SceneLocatorState:
    """节点：检测黑圈圆心坐标。"""
    if state["error"] is not None:
        return state

    try:
        # 读取环境母图和定位参考图
        env_bytes = _read_registered_bytes(
            file_storage,
            repo,
            state["environment_file_id"],
            state["destination_id"],
        )
        loc_bytes = _read_registered_bytes(
            file_storage,
            repo,
            state["locator_file_id"],
            state["destination_id"],
        )

        # 调用黑圈检测算法
        detection_result = detect_black_circle(
            env_bytes,
            loc_bytes,
            policy={"require_unique_candidate": True},
        )

        # 提取圆心坐标，并要求定位圆直径与正式配置一致。
        center_x, center_y = detection_result["planned_locator_center"]
        left, top, right, bottom = detection_result["bbox"]
        detected_diameter = max(right - left, bottom - top)
        if detected_diameter != state["interaction_diameter_px"]:
            state["error"] = "circle_size_mismatch"
            state["detection_diagnostics"] = {
                **detection_result,
                "detected_diameter": detected_diameter,
                "expected_diameter": state["interaction_diameter_px"],
            }
            return state

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
    vision_provider: ChatModelProvider | None = None,
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
        lambda state: generate_locator_node(
            state, repo, file_storage, vision_provider
        ),
    )
    workflow.add_node(
        "detect_circle_center",
        lambda state: detect_circle_center_node(state, repo, file_storage),
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
    vision_provider: ChatModelProvider | None = None,
    visual_anchor: dict[str, str] | None = None,
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
    env_artifact = repo.get_shared_environment_artifact(destination_id)
    if env_artifact is None or env_artifact["shared_environment_id"] != shared_environment_id:
        return {"status": "failed", "error": "shared_environment_not_found"}

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
        visual_anchor=visual_anchor,
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
    workflow = build_scene_locator_workflow(
        repo, file_storage, vision_provider=vision_provider
    )
    app = workflow.compile()

    final_state = app.invoke(initial_state)

    return final_state
