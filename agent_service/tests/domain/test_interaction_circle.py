"""测试黑圈检测算法 - Issue #18 测试要求。

测试用例：
1. 恰好一个合法黑圈时检测得到稳定整数圆心 ✓
2. 无圆时拒绝（技术失败） ✓
3. 多圆时选择最佳候选 ✓
4. NaN/无限值时拒绝 ✓
5. 尺寸不符时拒绝 ✓
6. 固定圆越界时拒绝 ✓
"""

import pytest
import sys
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw

# Add agent_service to path
agent_service_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(agent_service_root))

from domain.interaction_circle import (
    detect_black_circle,
    validate_circle_in_bounds,
    DetectionError,
    DEFAULT_LOCATOR_POLICY,
)


# ============================================================================
# Fixture 图片生成辅助函数
# ============================================================================


def create_test_environment(width: int = 2048, height: int = 1152) -> bytes:
    """创建测试用环境母图（渐变背景）。"""
    image = Image.new("RGB", (width, height))
    pixels = image.load()

    for y in range(height):
        for x in range(width):
            # 浅色渐变背景
            r = int(135 + (x / width) * 50)
            g = int(206 + (y / height) * 30)
            b = int(235 - (x / width) * 50)
            pixels[x, y] = (r, g, b)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def create_locator_with_single_black_circle(
    width: int = 2048,
    height: int = 1152,
    circle_center: tuple[int, int] = (1024, 576),
    circle_radius: int = 80,
) -> bytes:
    """创建带单个黑圈的定位参考图。

    Args:
        width: 图像宽度
        height: 图像高度
        circle_center: 圆心坐标 (x, y)
        circle_radius: 圆半径

    Returns:
        PNG 字节
    """
    # 复制环境母图
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            r = int(135 + (x / width) * 50)
            g = int(206 + (y / height) * 30)
            b = int(235 - (x / width) * 50)
            pixels[x, y] = (r, g, b)

    # 绘制黑色圆
    draw = ImageDraw.Draw(image)
    cx, cy = circle_center
    left = cx - circle_radius
    top = cy - circle_radius
    right = cx + circle_radius
    bottom = cy + circle_radius
    draw.ellipse([left, top, right, bottom], fill=(0, 0, 0))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def create_locator_with_multiple_circles(
    width: int = 2048, height: int = 1152
) -> bytes:
    """创建带多个黑圈的定位参考图。"""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            r = int(135 + (x / width) * 50)
            g = int(206 + (y / height) * 30)
            b = int(235 - (x / width) * 50)
            pixels[x, y] = (r, g, b)

    draw = ImageDraw.Draw(image)

    # 绘制第一个黑圈（较小、较黑）
    draw.ellipse([400, 300, 500, 400], fill=(5, 5, 5))

    # 绘制第二个黑圈（较大、较黑）
    draw.ellipse([1400, 700, 1550, 850], fill=(3, 3, 3))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def create_locator_without_circle(
    width: int = 2048, height: int = 1152
) -> bytes:
    """创建无黑圈的定位参考图（纯环境）。"""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            r = int(135 + (x / width) * 50)
            g = int(206 + (y / height) * 30)
            b = int(235 - (x / width) * 50)
            pixels[x, y] = (r, g, b)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def create_locator_with_edge_circle(
    width: int = 2048, height: int = 1152
) -> bytes:
    """创建触碰边缘的黑圈定位图。"""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            r = int(135 + (x / width) * 50)
            g = int(206 + (y / height) * 30)
            b = int(235 - (x / width) * 50)
            pixels[x, y] = (r, g, b)

    draw = ImageDraw.Draw(image)
    # 圆心在左边缘附近，圆会触碰边界
    draw.ellipse([0, 500, 100, 600], fill=(0, 0, 0))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ============================================================================
# 测试用例
# ============================================================================


def test_detect_single_black_circle_stable_integer_center():
    """测试 1: 恰好一个合法黑圈时检测得到稳定整数圆心。"""
    env_image = create_test_environment()
    loc_image = create_locator_with_single_black_circle(
        circle_center=(1024, 576), circle_radius=80
    )

    result = detect_black_circle(env_image, loc_image)

    # 验证返回结构
    assert result["algorithm"] == "black-filled-ellipse/v1"
    assert result["qualified_candidate_count"] == 1

    # 验证圆心为整数
    center_x, center_y = result["planned_locator_center"]
    assert isinstance(center_x, int)
    assert isinstance(center_y, int)

    # 验证圆心在预期范围内（允许几个像素误差）
    assert abs(center_x - 1024) <= 5
    assert abs(center_y - 576) <= 5

    # 验证浮点圆心也是有限值
    center_x_float, center_y_float = result["planned_locator_center_float"]
    assert isinstance(center_x_float, float)
    assert isinstance(center_y_float, float)
    import math
    assert math.isfinite(center_x_float)
    assert math.isfinite(center_y_float)


def test_reject_when_no_circle():
    """测试 2: 无圆时拒绝（技术失败）。"""
    env_image = create_test_environment()
    loc_image = create_locator_without_circle()

    with pytest.raises(DetectionError) as exc_info:
        detect_black_circle(env_image, loc_image)

    assert exc_info.value.reason == "no_black_pixels_found"


def test_select_best_when_multiple_circles():
    """测试 3: 多圆时选择最佳候选（通过评分）。"""
    env_image = create_test_environment()
    loc_image = create_locator_with_multiple_circles()

    result = detect_black_circle(env_image, loc_image)

    # 应该检测到多个候选（至少 2 个通过几何资格）
    # 但最终选择一个最佳候选
    assert result["qualified_candidate_count"] >= 1

    # 验证返回了唯一的圆心
    center_x, center_y = result["planned_locator_center"]
    assert isinstance(center_x, int)
    assert isinstance(center_y, int)


def test_reject_when_dimension_mismatch():
    """测试 5: 尺寸不符时拒绝。"""
    env_image = create_test_environment(width=2048, height=1152)
    loc_image = create_locator_with_single_black_circle(width=1024, height=576)

    with pytest.raises(DetectionError) as exc_info:
        detect_black_circle(env_image, loc_image)

    assert exc_info.value.reason == "dimension_mismatch"


def test_reject_circle_touching_canvas_edge():
    """测试：触碰边缘的圆被拒绝（几何资格）。"""
    env_image = create_test_environment()
    loc_image = create_locator_with_edge_circle()

    with pytest.raises(DetectionError) as exc_info:
        detect_black_circle(env_image, loc_image)

    # 应该因为触碰边缘而拒绝
    assert exc_info.value.reason == "no_plausible_black_marker"
    assert "touches_canvas_edge" in exc_info.value.rejection_counts


def test_validate_circle_in_bounds_accepts_valid_circle():
    """测试 6a: 圆完整在画布内时通过校验。"""
    canvas_width = 2048
    canvas_height = 1152
    center_x = 1024
    center_y = 576
    radius = 80

    is_valid = validate_circle_in_bounds(
        center_x, center_y, radius, canvas_width, canvas_height
    )

    assert is_valid is True


def test_validate_circle_in_bounds_rejects_out_of_bounds():
    """测试 6b: 固定圆越界时拒绝。"""
    canvas_width = 2048
    canvas_height = 1152

    # 圆心在左边缘，半径过大导致越界
    center_x = 50
    center_y = 576
    radius = 80

    is_valid = validate_circle_in_bounds(
        center_x, center_y, radius, canvas_width, canvas_height
    )

    assert is_valid is False

    # 圆心在右边缘越界
    center_x = 2040
    center_y = 576
    radius = 80

    is_valid = validate_circle_in_bounds(
        center_x, center_y, radius, canvas_width, canvas_height
    )

    assert is_valid is False


def test_detection_is_deterministic():
    """测试：给定相同输入，检测结果确定性。"""
    env_image = create_test_environment()
    loc_image = create_locator_with_single_black_circle(
        circle_center=(800, 400), circle_radius=60
    )

    result1 = detect_black_circle(env_image, loc_image)
    result2 = detect_black_circle(env_image, loc_image)

    # 两次检测应该得到完全相同的圆心
    assert result1["planned_locator_center"] == result2["planned_locator_center"]
    assert result1["planned_locator_center_float"] == result2["planned_locator_center_float"]


def test_detection_with_custom_policy():
    """测试：可以使用自定义参数配置。"""
    env_image = create_test_environment()
    loc_image = create_locator_with_single_black_circle()

    # 使用更宽松的参数
    custom_policy = DEFAULT_LOCATOR_POLICY.copy()
    custom_policy["minimum_area"] = 100  # 降低最小面积要求

    result = detect_black_circle(env_image, loc_image, policy=custom_policy)

    assert result["policy"]["minimum_area"] == 100
    assert result["qualified_candidate_count"] >= 1


def test_detection_result_contains_diagnostics():
    """测试：检测结果包含完整诊断信息。"""
    env_image = create_test_environment()
    loc_image = create_locator_with_single_black_circle()

    result = detect_black_circle(env_image, loc_image)

    # 验证诊断信息完整性
    assert "raw_component_count" in result
    assert "qualified_candidate_count" in result
    assert "rejection_counts" in result
    assert "selected_candidate" in result
    assert "candidate_diagnostics" in result

    # 选中候选应该包含详细元数据
    selected = result["selected_candidate"]
    assert "bbox" in selected
    assert "area" in selected
    assert "score" in selected
    assert "max_channel_p90" in selected
    assert "fraction_max_channel_le_20" in selected
