"""独立测试脚本 - 验证黑圈检测算法。

运行方式：python tests/domain/standalone_test_circle.py
"""

import sys
from pathlib import Path

# Add agent_service to path
agent_service_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(agent_service_root))

from io import BytesIO
from PIL import Image, ImageDraw
from domain.interaction_circle import (
    detect_black_circle,
    validate_circle_in_bounds,
    DetectionError,
)


# ============================================================================
# 辅助函数
# ============================================================================


def create_test_environment(width=2048, height=1152):
    """创建测试用环境母图。"""
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


def create_locator_with_single_black_circle(
    width=2048, height=1152, circle_center=(1024, 576), circle_radius=80
):
    """创建带单个黑圈的定位参考图。"""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            r = int(135 + (x / width) * 50)
            g = int(206 + (y / height) * 30)
            b = int(235 - (x / width) * 50)
            pixels[x, y] = (r, g, b)
    draw = ImageDraw.Draw(image)
    cx, cy = circle_center
    left, top = cx - circle_radius, cy - circle_radius
    right, bottom = cx + circle_radius, cy + circle_radius
    draw.ellipse([left, top, right, bottom], fill=(0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def create_locator_without_circle(width=2048, height=1152):
    """创建无黑圈的定位参考图。"""
    return create_test_environment(width, height)


def create_locator_with_multiple_circles(width=2048, height=1152):
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
    draw.ellipse([400, 300, 500, 400], fill=(5, 5, 5))
    draw.ellipse([1400, 700, 1550, 850], fill=(3, 3, 3))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def create_locator_with_edge_circle(width=2048, height=1152):
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
    draw.ellipse([0, 500, 100, 600], fill=(0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# ============================================================================
# 测试用例
# ============================================================================


def test_1_single_black_circle():
    """测试 1: 恰好一个合法黑圈时检测得到稳定整数圆心。"""
    print("\n[Test 1] Single black circle detection...")
    env_image = create_test_environment()
    loc_image = create_locator_with_single_black_circle(
        circle_center=(1024, 576), circle_radius=80
    )

    result = detect_black_circle(env_image, loc_image)

    assert result["algorithm"] == "black-filled-ellipse/v1"
    assert result["qualified_candidate_count"] == 1

    center_x, center_y = result["planned_locator_center"]
    assert isinstance(center_x, int)
    assert isinstance(center_y, int)
    assert abs(center_x - 1024) <= 5
    assert abs(center_y - 576) <= 5

    print(f"  PASS - Center: {result['planned_locator_center']}")


def test_2_no_circle():
    """测试 2: 无圆时拒绝（技术失败）。"""
    print("\n[Test 2] No circle rejection...")
    env_image = create_test_environment()
    loc_image = create_locator_without_circle()

    try:
        detect_black_circle(env_image, loc_image)
        assert False, "Should have raised DetectionError"
    except DetectionError as e:
        assert e.reason == "no_black_pixels_found"
        print(f"  PASS - Rejected with reason: {e.reason}")


def test_3_multiple_circles():
    """测试 3: 多圆时选择最佳候选。"""
    print("\n[Test 3] Multiple circles selection...")
    env_image = create_test_environment()
    loc_image = create_locator_with_multiple_circles()

    result = detect_black_circle(env_image, loc_image)

    assert result["qualified_candidate_count"] >= 1
    center_x, center_y = result["planned_locator_center"]
    assert isinstance(center_x, int)
    assert isinstance(center_y, int)

    print(f"  PASS - Selected from {result['qualified_candidate_count']} candidates")
    print(f"  Center: {result['planned_locator_center']}")


def test_4_dimension_mismatch():
    """测试 4: 尺寸不符时拒绝。"""
    print("\n[Test 4] Dimension mismatch rejection...")
    env_image = create_test_environment(width=2048, height=1152)
    loc_image = create_locator_with_single_black_circle(width=1024, height=576)

    try:
        detect_black_circle(env_image, loc_image)
        assert False, "Should have raised DetectionError"
    except DetectionError as e:
        assert e.reason == "dimension_mismatch"
        print(f"  PASS - Rejected with reason: {e.reason}")


def test_5_edge_circle():
    """测试 5: 触碰边缘的圆被拒绝。"""
    print("\n[Test 5] Edge circle rejection...")
    env_image = create_test_environment()
    loc_image = create_locator_with_edge_circle()

    try:
        detect_black_circle(env_image, loc_image)
        assert False, "Should have raised DetectionError"
    except DetectionError as e:
        assert e.reason == "no_plausible_black_marker"
        assert "touches_canvas_edge" in e.rejection_counts
        print(f"  PASS - Rejected: {e.rejection_counts}")


def test_6_circle_bounds_validation():
    """测试 6: 圆边界校验。"""
    print("\n[Test 6] Circle bounds validation...")

    # 6a: 合法圆
    is_valid = validate_circle_in_bounds(1024, 576, 80, 2048, 1152)
    assert is_valid is True
    print("  PASS - Valid circle accepted")

    # 6b: 越界圆
    is_valid = validate_circle_in_bounds(50, 576, 80, 2048, 1152)
    assert is_valid is False
    print("  PASS - Out-of-bounds circle rejected")


def test_7_deterministic():
    """测试 7: 检测结果确定性。"""
    print("\n[Test 7] Deterministic detection...")
    env_image = create_test_environment()
    loc_image = create_locator_with_single_black_circle(
        circle_center=(800, 400), circle_radius=60
    )

    result1 = detect_black_circle(env_image, loc_image)
    result2 = detect_black_circle(env_image, loc_image)

    assert result1["planned_locator_center"] == result2["planned_locator_center"]
    assert result1["planned_locator_center_float"] == result2["planned_locator_center_float"]

    print(f"  PASS - Results consistent: {result1['planned_locator_center']}")


def test_8_diagnostics():
    """测试 8: 诊断信息完整性。"""
    print("\n[Test 8] Diagnostics completeness...")
    env_image = create_test_environment()
    loc_image = create_locator_with_single_black_circle()

    result = detect_black_circle(env_image, loc_image)

    assert "raw_component_count" in result
    assert "qualified_candidate_count" in result
    assert "rejection_counts" in result
    assert "selected_candidate" in result
    assert "candidate_diagnostics" in result

    selected = result["selected_candidate"]
    assert "bbox" in selected
    assert "area" in selected
    assert "score" in selected

    print("  PASS - All diagnostic fields present")


# ============================================================================
# 主函数
# ============================================================================


if __name__ == "__main__":
    print("=" * 70)
    print("黑圈检测算法测试 (Issue #18)")
    print("=" * 70)

    tests = [
        test_1_single_black_circle,
        test_2_no_circle,
        test_3_multiple_circles,
        test_4_dimension_mismatch,
        test_5_edge_circle,
        test_6_circle_bounds_validation,
        test_7_deterministic,
        test_8_diagnostics,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL - {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed == 0:
        print("\n✓ All tests passed!")
        sys.exit(0)
    else:
        print(f"\n✗ {failed} tests failed")
        sys.exit(1)
