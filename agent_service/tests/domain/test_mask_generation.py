"""测试 Mask 生成算法（T7 - Issue #19）。

测试要点：
1. 字节稳定性：相同输入 → 相同字节输出
2. InteractionZone 与 Mask 使用同一 center/radius
3. pixel_top_left 坐标系统
4. 直径必须是偶数
5. 圆必须完全在画布内
"""

from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from PIL import Image

from agent_service.domain.mask_generation import (
    generate_mask_and_aperture,
    compute_interaction_diameter,
)


# ============================================================================
# 测试 Fixture
# ============================================================================


@pytest.fixture
def sample_environment_image() -> bytes:
    """创建 2048x1152 的测试环境图。"""
    img = Image.new("RGB", (2048, 1152), color=(100, 150, 200))
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# ============================================================================
# 测试用例 1: 字节稳定性
# ============================================================================


def test_mask_generation_is_byte_stable(sample_environment_image):
    """测试：相同输入产生相同字节输出。"""
    center_x = 1024
    center_y = 576
    diameter = 160

    # 第一次生成
    result1 = generate_mask_and_aperture(
        sample_environment_image, center_x, center_y, diameter
    )

    # 第二次生成（相同输入）
    result2 = generate_mask_and_aperture(
        sample_environment_image, center_x, center_y, diameter
    )

    # 验证字节稳定
    assert result1["generation_mask_bytes"] == result2["generation_mask_bytes"]
    assert result1["generation_mask_sha256"] == result2["generation_mask_sha256"]
    assert result1["aperture_image_bytes"] == result2["aperture_image_bytes"]
    assert result1["aperture_image_sha256"] == result2["aperture_image_sha256"]


def test_mask_generation_different_input_different_output(sample_environment_image):
    """测试：不同输入产生不同输出。"""
    result1 = generate_mask_and_aperture(sample_environment_image, 1024, 576, 160)
    result2 = generate_mask_and_aperture(sample_environment_image, 1000, 576, 160)

    # 不同圆心应产生不同 Mask
    assert result1["generation_mask_sha256"] != result2["generation_mask_sha256"]
    assert result1["aperture_image_sha256"] != result2["aperture_image_sha256"]


# ============================================================================
# 测试用例 2: InteractionZone 与 Mask 一致性
# ============================================================================


def test_interaction_zone_matches_mask_geometry(sample_environment_image):
    """测试：InteractionZone 与 Mask 使用相同的 center/radius。"""
    center_x = 1024
    center_y = 576
    diameter = 160

    result = generate_mask_and_aperture(
        sample_environment_image, center_x, center_y, diameter
    )

    # 验证几何参数
    assert result["center_x_px"] == center_x
    assert result["center_y_px"] == center_y
    assert result["radius_px"] == diameter // 2
    assert result["diameter_px"] == diameter


# ============================================================================
# 测试用例 3: 坐标系统
# ============================================================================


def test_coordinate_space_is_pixel_top_left(sample_environment_image):
    """测试：使用 pixel_top_left 坐标系统。"""
    result = generate_mask_and_aperture(sample_environment_image, 1024, 576, 160)

    assert result["coordinate_space"] == "pixel_top_left"


# ============================================================================
# 测试用例 4: 直径验证
# ============================================================================


def test_diameter_must_be_even(sample_environment_image):
    """测试：直径必须是偶数。"""
    with pytest.raises(ValueError, match="必须是偶数"):
        generate_mask_and_aperture(sample_environment_image, 1024, 576, 159)


def test_diameter_even_accepted(sample_environment_image):
    """测试：偶数直径被接受。"""
    result = generate_mask_and_aperture(sample_environment_image, 1024, 576, 160)
    assert result["diameter_px"] == 160


# ============================================================================
# 测试用例 5: 边界验证
# ============================================================================


def test_circle_must_be_inside_canvas(sample_environment_image):
    """测试：圆必须完全在画布内。"""
    # 圆心太靠左
    with pytest.raises(ValueError, match="圆左边界超出画布"):
        generate_mask_and_aperture(sample_environment_image, 50, 576, 160)

    # 圆心太靠上
    with pytest.raises(ValueError, match="圆上边界超出画布"):
        generate_mask_and_aperture(sample_environment_image, 1024, 50, 160)

    # 圆心太靠右
    with pytest.raises(ValueError, match="圆右边界超出画布"):
        generate_mask_and_aperture(sample_environment_image, 2000, 576, 160)

    # 圆心太靠下
    with pytest.raises(ValueError, match="圆下边界超出画布"):
        generate_mask_and_aperture(sample_environment_image, 1024, 1100, 160)


def test_circle_at_valid_boundary(sample_environment_image):
    """测试：圆刚好在画布边界内（有效）。"""
    # 左边界：center_x = radius
    result = generate_mask_and_aperture(sample_environment_image, 80, 576, 160)
    assert result["center_x_px"] == 80

    # 上边界：center_y = radius
    result = generate_mask_and_aperture(sample_environment_image, 1024, 80, 160)
    assert result["center_y_px"] == 80

    # 右边界：center_x = width - radius
    result = generate_mask_and_aperture(sample_environment_image, 2048 - 80, 576, 160)
    assert result["center_x_px"] == 2048 - 80

    # 下边界：center_y = height - radius
    result = generate_mask_and_aperture(sample_environment_image, 1024, 1152 - 80, 160)
    assert result["center_y_px"] == 1152 - 80


# ============================================================================
# 测试用例 6: Mask 内容验证
# ============================================================================


def test_generation_mask_is_binary(sample_environment_image):
    """测试：generation mask 是二值图（黑白）。"""
    result = generate_mask_and_aperture(sample_environment_image, 1024, 576, 160)

    mask_img = Image.open(BytesIO(result["generation_mask_bytes"]))
    assert mask_img.mode == "L"  # 灰度图

    # 检查像素值只有 0 和 255
    pixels = list(mask_img.getdata())
    unique_values = set(pixels)
    assert unique_values.issubset({0, 255})


def test_mask_circle_is_white_outside_is_black(sample_environment_image):
    """测试：Mask 圆内白色（255），圆外黑色（0）。"""
    center_x = 1024
    center_y = 576
    radius = 80

    result = generate_mask_and_aperture(sample_environment_image, center_x, center_y, 160)

    mask_img = Image.open(BytesIO(result["generation_mask_bytes"]))

    # 检查圆心：应该是白色
    center_pixel = mask_img.getpixel((center_x, center_y))
    assert center_pixel == 255

    # 检查圆内某点：应该是白色
    inside_pixel = mask_img.getpixel((center_x + 40, center_y))
    assert inside_pixel == 255

    # 检查圆外某点：应该是黑色
    outside_pixel = mask_img.getpixel((center_x + 200, center_y))
    assert outside_pixel == 0


# ============================================================================
# 测试用例 7: Aperture 内容验证
# ============================================================================


def test_aperture_has_black_circle(sample_environment_image):
    """测试：aperture 图在指定位置有黑色圆。"""
    center_x = 1024
    center_y = 576

    result = generate_mask_and_aperture(sample_environment_image, center_x, center_y, 160)

    aperture_img = Image.open(BytesIO(result["aperture_image_bytes"]))

    # 检查圆心：应该是黑色
    center_pixel = aperture_img.getpixel((center_x, center_y))
    assert center_pixel == (0, 0, 0)

    # 检查圆内某点：应该是黑色
    inside_pixel = aperture_img.getpixel((center_x + 40, center_y))
    assert inside_pixel == (0, 0, 0)

    # 检查圆外某点：应该保持原环境颜色（100, 150, 200）
    outside_pixel = aperture_img.getpixel((center_x + 200, center_y))
    assert outside_pixel == (100, 150, 200)


# ============================================================================
# 测试用例 8: 直径计算
# ============================================================================


def test_compute_interaction_diameter_returns_even():
    """测试：计算的直径总是偶数。"""
    diameter = compute_interaction_diameter(2048, 1152, 0.14)
    assert diameter % 2 == 0


def test_compute_interaction_diameter_matches_protocol():
    """测试：直径计算符合协议（short_edge * 0.14）。"""
    # 对于 2048x1152，short_edge = 1152
    # 1152 * 0.14 = 161.28 → 最近偶数 = 160
    diameter = compute_interaction_diameter(2048, 1152, 0.14)
    assert diameter == 160


def test_compute_interaction_diameter_different_sizes():
    """测试：不同尺寸产生不同直径。"""
    d1 = compute_interaction_diameter(1024, 768, 0.14)
    d2 = compute_interaction_diameter(2048, 1152, 0.14)

    assert d1 < d2
    assert d1 % 2 == 0
    assert d2 % 2 == 0


# ============================================================================
# 测试用例 9: 画布尺寸记录
# ============================================================================


def test_canvas_dimensions_recorded(sample_environment_image):
    """测试：画布尺寸正确记录。"""
    result = generate_mask_and_aperture(sample_environment_image, 1024, 576, 160)

    assert result["canvas_width_px"] == 2048
    assert result["canvas_height_px"] == 1152


# ============================================================================
# 测试用例 10: SHA256 哈希正确性
# ============================================================================


def test_sha256_hashes_are_correct(sample_environment_image):
    """测试：SHA256 哈希正确计算。"""
    result = generate_mask_and_aperture(sample_environment_image, 1024, 576, 160)

    # 验证 generation_mask 哈希
    actual_mask_hash = hashlib.sha256(result["generation_mask_bytes"]).hexdigest()
    assert result["generation_mask_sha256"] == actual_mask_hash

    # 验证 aperture 哈希
    actual_aperture_hash = hashlib.sha256(result["aperture_image_bytes"]).hexdigest()
    assert result["aperture_image_sha256"] == actual_aperture_hash
