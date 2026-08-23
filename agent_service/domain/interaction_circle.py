"""黑圈检测算法 - black-filled-ellipse/v1

纯函数实现，从定位参考图中检测黑色填充椭圆标记并确定整数圆心坐标。

算法来源：Issue #12 原型验证，成功率 16/16 (100%)
参考文档：docs/reference-black-circle-detection.md
协议定义：docs/contracts/dual-scene-generation-protocol-v0.1.md 第 3 节
"""

from __future__ import annotations

import math
from typing import Any, TypedDict


# ============================================================================
# 默认参数配置
# ============================================================================

DEFAULT_LOCATOR_POLICY = {
    "algorithm": "black-filled-ellipse/v1",
    "candidate_m_max": 20,  # 候选像素 max(R,G,B) 上限
    "minimum_area": 500,  # 最小组件面积（像素²）
    "minimum_bbox_side": 20,  # bbox 最小边（像素）
    "aspect_min": 0.25,  # 长宽比下限（允许 1:4 扁平）
    "aspect_max": 4.0,  # 长宽比上限（允许 4:1 狭长）
    "fill_min": 0.70,  # 填充率下限
    "fill_max": 0.90,  # 填充率上限
    "reject_canvas_edge": True,  # 拒绝触碰边缘的候选
    "ellipse_m_p90_max": 12,  # 椭圆内黑色深度上限
    "ellipse_chroma_p90_max": 6,  # 椭圆内色偏上限
    "ellipse_q20_min": 0.94,  # 椭圆内黑色覆盖下限
    "delta_y_mean_min": 40,  # 相对环境变暗下限
}


# ============================================================================
# 返回类型
# ============================================================================


class DetectionResult(TypedDict):
    """黑圈检测结果。"""

    algorithm: str
    policy: dict[str, Any]
    raw_component_count: int
    qualified_candidate_count: int
    rejection_counts: dict[str, int]
    selected_candidate: dict[str, Any]
    candidate_diagnostics: list[dict[str, Any]]
    planned_locator_center_float: tuple[float, float]
    planned_locator_center: tuple[int, int]
    bbox: tuple[int, int, int, int]
    area: int


class DetectionError(Exception):
    """检测失败异常。"""

    def __init__(self, reason: str, rejection_counts: dict[str, int] | None = None):
        self.reason = reason
        self.rejection_counts = rejection_counts or {}
        super().__init__(reason)


# ============================================================================
# 辅助函数
# ============================================================================


def half_up(value: float) -> int:
    """半数进位舍入（0.5 向上）。"""
    return math.floor(value + 0.5)


def _percentile(values: list[float], fraction: float) -> float:
    """计算百分位数（线性插值）。

    Args:
        values: 已排序的数值列表
        fraction: 百分位（0.0-1.0）

    Returns:
        百分位数值
    """
    if not values:
        return 0.0

    if len(values) == 1:
        return values[0]

    # 线性插值
    index = fraction * (len(values) - 1)
    lower = int(math.floor(index))
    upper = int(math.ceil(index))

    if lower == upper:
        return values[lower]

    weight = index - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _compute_luminance(r: int, g: int, b: int) -> float:
    """计算 Rec.709 亮度。"""
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# ============================================================================
# 8-连通分量检测
# ============================================================================


def _find_connected_components(
    width: int, height: int, is_candidate_pixel: list[list[bool]]
) -> list[list[tuple[int, int]]]:
    """8-连通分量检测（flood-fill）。

    Args:
        width: 图像宽度
        height: 图像高度
        is_candidate_pixel: 二维布尔数组，标记候选像素

    Returns:
        组件列表，每个组件是像素坐标列表 [(x, y), ...]
    """
    visited = [[False] * width for _ in range(height)]
    components = []

    def flood_fill(start_x: int, start_y: int) -> list[tuple[int, int]]:
        """从起点进行 flood-fill，返回连通组件。"""
        stack = [(start_x, start_y)]
        component = []

        while stack:
            x, y = stack.pop()

            if x < 0 or x >= width or y < 0 or y >= height:
                continue
            if visited[y][x] or not is_candidate_pixel[y][x]:
                continue

            visited[y][x] = True
            component.append((x, y))

            # 8-连通（包括对角）
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    stack.append((x + dx, y + dy))

        return component

    # 遍历所有像素，找到未访问的候选像素作为组件起点
    for y in range(height):
        for x in range(width):
            if is_candidate_pixel[y][x] and not visited[y][x]:
                component = flood_fill(x, y)
                if component:
                    components.append(component)

    return components


# ============================================================================
# 几何资格筛选
# ============================================================================


def _compute_bbox(component: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    """计算组件的外接矩形。

    Returns:
        (left, top, right, bottom)
    """
    xs = [x for x, y in component]
    ys = [y for x, y in component]
    return (min(xs), min(ys), max(xs), max(ys))


def _check_geometric_qualification(
    component: list[tuple[int, int]],
    canvas_width: int,
    canvas_height: int,
    policy: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    """检查组件的几何资格。

    Returns:
        (is_qualified, rejection_reason, metadata)
    """
    area = len(component)
    left, top, right, bottom = _compute_bbox(component)
    bbox_width = right - left + 1
    bbox_height = bottom - top + 1
    bbox_area = bbox_width * bbox_height

    # 1. 面积约束
    if area < policy["minimum_area"]:
        return False, "area_too_small", {}

    # 2. bbox 最小边
    min_side = min(bbox_width, bbox_height)
    if min_side < policy["minimum_bbox_side"]:
        return False, "bbox_too_small", {}

    # 3. 长宽比
    max_side = max(bbox_width, bbox_height)
    aspect = max_side / min_side if min_side > 0 else float("inf")
    if aspect < policy["aspect_min"] or aspect > policy["aspect_max"]:
        return False, "aspect_out_of_range", {}

    # 4. 填充率
    fill = area / bbox_area if bbox_area > 0 else 0
    if fill < policy["fill_min"] or fill > policy["fill_max"]:
        return False, "fill_out_of_range", {}

    # 5. 边缘约束
    if policy["reject_canvas_edge"]:
        if left == 0 or top == 0 or right == canvas_width - 1 or bottom == canvas_height - 1:
            return False, "touches_canvas_edge", {}

    # 通过几何资格
    metadata = {
        "area": area,
        "bbox": (left, top, right, bottom),
        "bbox_width": bbox_width,
        "bbox_height": bbox_height,
        "aspect": aspect,
        "fill": fill,
    }
    return True, None, metadata


# ============================================================================
# 椭圆 Mask 构建与颜色资格筛选
# ============================================================================


def _build_ellipse_mask(
    bbox: tuple[int, int, int, int]
) -> list[tuple[int, int]]:
    """构建 bbox 内接椭圆的像素坐标列表。

    Args:
        bbox: (left, top, right, bottom)

    Returns:
        椭圆内像素坐标列表 [(x, y), ...]
    """
    left, top, right, bottom = bbox
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    radius_x = (right - left + 1) / 2.0
    radius_y = (bottom - top + 1) / 2.0

    mask_pixels = []
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            # 椭圆方程：((x-cx)/rx)² + ((y-cy)/ry)² <= 1
            normalized = ((x - center_x) / radius_x) ** 2 + ((y - center_y) / radius_y) ** 2
            if normalized <= 1.0:
                mask_pixels.append((x, y))

    return mask_pixels


def _check_color_qualification(
    component: list[tuple[int, int]],
    bbox: tuple[int, int, int, int],
    locator_pixels: list[list[tuple[int, int, int]]],
    environment_pixels: list[list[tuple[int, int, int]]],
    policy: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    """检查组件的颜色资格（在椭圆 mask 内统计）。

    Returns:
        (is_qualified, rejection_reason, color_metadata)
    """
    ellipse_mask = _build_ellipse_mask(bbox)

    if not ellipse_mask:
        return False, "ellipse_mask_empty", {}

    # 提取椭圆内像素的颜色特征
    marker_max_values = []
    chroma_values = []
    locator_luminances = []
    environment_luminances = []

    for x, y in ellipse_mask:
        r_loc, g_loc, b_loc = locator_pixels[y][x]
        r_env, g_env, b_env = environment_pixels[y][x]

        # max(R, G, B)
        m = max(r_loc, g_loc, b_loc)
        marker_max_values.append(m)

        # chroma = max - min
        chroma = m - min(r_loc, g_loc, b_loc)
        chroma_values.append(chroma)

        # luminance
        loc_lum = _compute_luminance(r_loc, g_loc, b_loc)
        env_lum = _compute_luminance(r_env, g_env, b_env)
        locator_luminances.append(loc_lum)
        environment_luminances.append(env_lum)

    # 排序用于百分位计算
    marker_max_sorted = sorted(marker_max_values)
    chroma_sorted = sorted(chroma_values)
    locator_lum_sorted = sorted(locator_luminances)

    # 统计指标
    m_p90 = _percentile(marker_max_sorted, 0.90)
    chroma_p90 = _percentile(chroma_sorted, 0.90)
    q20_count = sum(1 for m in marker_max_values if m <= 20)
    q20_fraction = q20_count / len(marker_max_values) if marker_max_values else 0

    # 平均亮度差
    mean_loc_lum = sum(locator_luminances) / len(locator_luminances) if locator_luminances else 0
    mean_env_lum = sum(environment_luminances) / len(environment_luminances) if environment_luminances else 0
    delta_y_mean = mean_env_lum - mean_loc_lum

    # IQR (四分位距)
    q25 = _percentile(locator_lum_sorted, 0.25)
    q75 = _percentile(locator_lum_sorted, 0.75)
    luminance_iqr = q75 - q25

    # 颜色资格硬门槛
    # 1. 黑色深度
    if m_p90 > policy["ellipse_m_p90_max"]:
        return False, "not_black_enough", {}

    # 2. 低色偏
    if chroma_p90 > policy["ellipse_chroma_p90_max"]:
        return False, "too_much_chroma", {}

    # 3. 黑色覆盖均匀度
    if q20_fraction < policy["ellipse_q20_min"]:
        return False, "insufficient_black_coverage", {}

    # 4. 相对变暗
    if delta_y_mean < policy["delta_y_mean_min"]:
        return False, "insufficient_darkening", {}

    # 5. 椭圆覆盖率
    component_area = len(component)
    ellipse_area = len(ellipse_mask)
    ellipse_coverage = component_area / ellipse_area if ellipse_area > 0 else 0
    if ellipse_coverage < 0.70 or ellipse_coverage > 1.15:
        return False, "ellipse_coverage_out_of_range", {}

    # 通过颜色资格
    color_metadata = {
        "max_channel_p90": m_p90,
        "chroma_p90": chroma_p90,
        "fraction_max_channel_le_20": q20_fraction,
        "delta_y_mean": delta_y_mean,
        "luminance_iqr": luminance_iqr,
        "ellipse_coverage": ellipse_coverage,
        "ellipse_area": ellipse_area,
    }
    return True, None, color_metadata


# ============================================================================
# 多维度评分
# ============================================================================


def _compute_score(metadata: dict[str, Any]) -> float:
    """计算候选的综合评分。

    score = 0.25×blackness + 0.25×coverage + 0.15×neutrality
          + 0.10×uniformity + 0.15×darkening + 0.10×solidity
    """
    m_p90 = metadata["max_channel_p90"]
    q20 = metadata["fraction_max_channel_le_20"]
    chroma_p90 = metadata["chroma_p90"]
    luminance_iqr = metadata["luminance_iqr"]
    delta_y_mean = metadata["delta_y_mean"]
    fill = metadata["fill"]

    # 归一化分量（clamp 到 [0, 1]）
    blackness = max(0.0, min(1.0, (20 - m_p90) / 12))
    coverage = max(0.0, min(1.0, (q20 - 0.80) / 0.18))
    neutrality = max(0.0, min(1.0, (10 - chroma_p90) / 8))
    uniformity = max(0.0, min(1.0, (8 - luminance_iqr) / 6))
    darkening = max(0.0, min(1.0, (delta_y_mean - 20) / 50))
    solidity = max(0.0, min(1.0, (fill - 0.55) / 0.22))

    score = (
        0.25 * blackness
        + 0.25 * coverage
        + 0.15 * neutrality
        + 0.10 * uniformity
        + 0.15 * darkening
        + 0.10 * solidity
    )

    return score


def _select_best_candidate(
    qualified_candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    """从合格候选中选择最佳（评分 + tie-break）。

    Tie-break 顺序：
    1. score 降序
    2. fraction_max_channel_le_20 降序
    3. max_channel_p90 升序
    4. area 降序
    5. bbox[1] (top) 升序
    6. bbox[0] (left) 升序
    7. bbox[3] (bottom) 升序
    8. bbox[2] (right) 升序
    """

    def sort_key(candidate: dict[str, Any]) -> tuple:
        bbox = candidate["bbox"]
        return (
            -candidate["score"],  # 降序
            -candidate["fraction_max_channel_le_20"],  # 降序
            candidate["max_channel_p90"],  # 升序
            -candidate["area"],  # 降序
            bbox[1],  # top 升序
            bbox[0],  # left 升序
            bbox[3],  # bottom 升序
            bbox[2],  # right 升序
        )

    sorted_candidates = sorted(qualified_candidates, key=sort_key)
    return sorted_candidates[0]


# ============================================================================
# 主检测函数
# ============================================================================


def detect_black_circle(
    environment_image_bytes: bytes,
    locator_image_bytes: bytes,
    policy: dict[str, Any] | None = None,
) -> DetectionResult:
    """检测定位参考图中的黑色圆心坐标。

    Args:
        environment_image_bytes: 环境母图 PNG 字节
        locator_image_bytes: 定位参考图 PNG 字节
        policy: 检测参数配置（可选，默认使用 DEFAULT_LOCATOR_POLICY）

    Returns:
        DetectionResult 包含圆心坐标和诊断信息

    Raises:
        DetectionError: 检测失败（无候选通过资格、尺寸不符等）
    """
    from PIL import Image
    from io import BytesIO

    # 调用方只覆盖需要调整的策略项，其余沿用已版本化默认值。
    policy = {**DEFAULT_LOCATOR_POLICY, **(policy or {})}

    # 加载图像
    env_image = Image.open(BytesIO(environment_image_bytes)).convert("RGB")
    loc_image = Image.open(BytesIO(locator_image_bytes)).convert("RGB")

    env_width, env_height = env_image.size
    loc_width, loc_height = loc_image.size

    # 尺寸校验
    if env_width != loc_width or env_height != loc_height:
        raise DetectionError(
            "dimension_mismatch",
            {"environment": (env_width, env_height), "locator": (loc_width, loc_height)},
        )

    # 转换为像素数组
    env_pixels = [
        [env_image.getpixel((x, y)) for x in range(env_width)] for y in range(env_height)
    ]
    loc_pixels = [
        [loc_image.getpixel((x, y)) for x in range(loc_width)] for y in range(loc_height)
    ]

    # 第一阶段：提取黑色候选像素
    candidate_m_max = policy["candidate_m_max"]
    is_candidate = [
        [max(loc_pixels[y][x]) <= candidate_m_max for x in range(loc_width)]
        for y in range(loc_height)
    ]

    # 8-连通分量检测
    components = _find_connected_components(loc_width, loc_height, is_candidate)
    raw_component_count = len(components)

    if raw_component_count == 0:
        raise DetectionError("no_black_pixels_found", {"candidate_m_max": candidate_m_max})

    # 第二阶段：几何资格筛选
    rejection_counts: dict[str, int] = {}
    geometric_qualified = []

    for component in components:
        is_qualified, reason, metadata = _check_geometric_qualification(
            component, loc_width, loc_height, policy
        )
        if is_qualified:
            geometric_qualified.append({"component": component, "metadata": metadata})
        elif reason:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    if not geometric_qualified:
        raise DetectionError("no_plausible_black_marker", rejection_counts)

    # 第三阶段：颜色资格筛选
    color_qualified = []

    for candidate in geometric_qualified:
        component = candidate["component"]
        bbox = candidate["metadata"]["bbox"]
        is_qualified, reason, color_metadata = _check_color_qualification(
            component, bbox, loc_pixels, env_pixels, policy
        )
        if is_qualified:
            # 合并元数据
            merged_metadata = {**candidate["metadata"], **color_metadata}
            color_qualified.append({"component": component, "metadata": merged_metadata})
        elif reason:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    if not color_qualified:
        raise DetectionError("no_plausible_black_marker", rejection_counts)
    if policy.get("require_unique_candidate") and len(color_qualified) != 1:
        raise DetectionError(
            "multiple_plausible_black_markers",
            {"qualified_candidate_count": len(color_qualified)},
        )

    # 第四阶段：评分与选择
    for candidate in color_qualified:
        candidate["metadata"]["score"] = _compute_score(candidate["metadata"])

    best_candidate = _select_best_candidate([c["metadata"] for c in color_qualified])

    # 计算圆心坐标（椭圆中心 + half-up 舍入）
    left, top, right, bottom = best_candidate["bbox"]
    center_x_float = (left + right) / 2.0
    center_y_float = (top + bottom) / 2.0
    center_x = half_up(center_x_float)
    center_y = half_up(center_y_float)

    # 圆心有限性检查
    if not (math.isfinite(center_x_float) and math.isfinite(center_y_float)):
        raise DetectionError("center_not_finite", {})

    # 构建返回结果
    return DetectionResult(
        algorithm=policy["algorithm"],
        policy=policy,
        raw_component_count=raw_component_count,
        qualified_candidate_count=len(color_qualified),
        rejection_counts=rejection_counts,
        selected_candidate=best_candidate,
        candidate_diagnostics=[
            c["metadata"] for c in color_qualified[:20]
        ],  # 前 20 个候选
        planned_locator_center_float=(center_x_float, center_y_float),
        planned_locator_center=(center_x, center_y),
        bbox=best_candidate["bbox"],
        area=best_candidate["area"],
    )


def validate_circle_in_bounds(
    center_x: int,
    center_y: int,
    radius_px: int,
    canvas_width: int,
    canvas_height: int,
) -> bool:
    """验证圆形是否完整位于 canvas 内。

    Args:
        center_x: 圆心 x 坐标
        center_y: 圆心 y 坐标
        radius_px: 圆半径（像素）
        canvas_width: 画布宽度
        canvas_height: 画布高度

    Returns:
        True 如果圆完整在画布内，否则 False
    """
    if center_x - radius_px < 0:
        return False
    if center_x + radius_px >= canvas_width:
        return False
    if center_y - radius_px < 0:
        return False
    if center_y + radius_px >= canvas_height:
        return False
    return True
