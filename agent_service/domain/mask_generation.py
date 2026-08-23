"""Mask 生成算法 - 字节稳定的 Generation Mask（T7 - Issue #19）。

纯函数实现，从圆心坐标生成：
1. 二值 generation Mask（黑白 PNG）
2. 打洞参考图（aperture image）- 环境母图 + 黑色圆

所有输出来自同一次计算，保证一致性。
字节稳定：相同输入 → 相同字节输出。

协议参考：docs/contracts/dual-scene-generation-protocol-v0.1.md 第 4 节
"""

from __future__ import annotations

import math
from io import BytesIO
from typing import TypedDict

from PIL import Image, ImageDraw


# ============================================================================
# 返回类型
# ============================================================================


class MaskGenerationResult(TypedDict):
    """Mask 生成结果。"""

    # Generation Mask（二值图）
    generation_mask_bytes: bytes
    generation_mask_sha256: str

    # 打洞参考图（aperture image）
    aperture_image_bytes: bytes
    aperture_image_sha256: str

    # 几何参数（供 InteractionZone 使用）
    center_x_px: int
    center_y_px: int
    radius_px: int
    diameter_px: int

    # 元数据
    coordinate_space: str
    canvas_width_px: int
    canvas_height_px: int


# ============================================================================
# Mask 生成核心算法
# ============================================================================


def generate_mask_and_aperture(
    environment_image_bytes: bytes,
    center_x: int,
    center_y: int,
    diameter_px: int,
) -> MaskGenerationResult:
    """生成 Mask 和打洞参考图（字节稳定）。

    从环境母图和圆心坐标生成三者：
    1. generation_mask: 二值 PNG（圆内白色 255，圆外黑色 0）
    2. aperture_image: 环境母图 + 黑色圆（RGB，圆内纯黑 #000000）
    3. 几何参数: center、radius、diameter

    字节稳定性保证：
    - 使用确定性的图像库操作
    - 固定 PNG 压缩参数
    - 整数坐标和半径
    - 不依赖随机数或时间戳

    Args:
        environment_image_bytes: 环境母图 PNG 字节
        center_x: 圆心 X 坐标（pixel_top_left）
        center_y: 圆心 Y 坐标（pixel_top_left）
        diameter_px: 圆直径（像素，必须是偶数）

    Returns:
        MaskGenerationResult: 包含 Mask、aperture、几何参数和哈希

    Raises:
        ValueError: 如果直径不是偶数，或圆超出画布边界
    """
    # 验证直径是偶数
    if diameter_px % 2 != 0:
        raise ValueError(f"diameter_px 必须是偶数，得到 {diameter_px}")

    radius_px = diameter_px // 2

    # 加载环境母图
    env_image = Image.open(BytesIO(environment_image_bytes)).convert("RGB")
    canvas_width = env_image.width
    canvas_height = env_image.height

    # 验证圆在画布内
    if center_x - radius_px < 0:
        raise ValueError(f"圆左边界超出画布：center_x={center_x}, radius={radius_px}")
    if center_y - radius_px < 0:
        raise ValueError(f"圆上边界超出画布：center_y={center_y}, radius={radius_px}")
    if center_x + radius_px > canvas_width:
        raise ValueError(
            f"圆右边界超出画布：center_x={center_x}, radius={radius_px}, width={canvas_width}"
        )
    if center_y + radius_px > canvas_height:
        raise ValueError(
            f"圆下边界超出画布：center_y={center_y}, radius={radius_px}, height={canvas_height}"
        )

    # ========================================================================
    # 1. 生成二值 Generation Mask
    # ========================================================================

    # 创建黑色背景（圆外 = 0）
    mask_image = Image.new("L", (canvas_width, canvas_height), color=0)
    mask_draw = ImageDraw.Draw(mask_image)

    # 绘制白色圆（圆内 = 255）
    # PIL ellipse 坐标：左上角 (x0, y0), 右下角 (x1, y1)
    left = center_x - radius_px
    top = center_y - radius_px
    right = center_x + radius_px
    bottom = center_y + radius_px

    mask_draw.ellipse([left, top, right, bottom], fill=255)

    # 转换为 PNG 字节（字节稳定）
    mask_buffer = BytesIO()
    mask_image.save(
        mask_buffer,
        format="PNG",
        optimize=False,  # 禁用优化保证字节稳定
        compress_level=6,  # 固定压缩级别
    )
    generation_mask_bytes = mask_buffer.getvalue()

    # 计算哈希
    import hashlib

    generation_mask_sha256 = hashlib.sha256(generation_mask_bytes).hexdigest()

    # ========================================================================
    # 2. 生成打洞参考图（Aperture Image）
    # ========================================================================

    # 复制环境母图
    aperture_image = env_image.copy()
    aperture_draw = ImageDraw.Draw(aperture_image)

    # 在相同位置绘制黑色圆（#000000）
    aperture_draw.ellipse([left, top, right, bottom], fill=(0, 0, 0))

    # 转换为 PNG 字节（字节稳定）
    aperture_buffer = BytesIO()
    aperture_image.save(
        aperture_buffer,
        format="PNG",
        optimize=False,
        compress_level=6,
    )
    aperture_image_bytes = aperture_buffer.getvalue()

    # 计算哈希
    aperture_image_sha256 = hashlib.sha256(aperture_image_bytes).hexdigest()

    # ========================================================================
    # 3. 返回结果
    # ========================================================================

    return MaskGenerationResult(
        generation_mask_bytes=generation_mask_bytes,
        generation_mask_sha256=generation_mask_sha256,
        aperture_image_bytes=aperture_image_bytes,
        aperture_image_sha256=aperture_image_sha256,
        center_x_px=center_x,
        center_y_px=center_y,
        radius_px=radius_px,
        diameter_px=diameter_px,
        coordinate_space="pixel_top_left",
        canvas_width_px=canvas_width,
        canvas_height_px=canvas_height,
    )


def compute_interaction_diameter(
    environment_width: int,
    environment_height: int,
    short_edge_ratio: float = 0.14,
) -> int:
    """计算交互圆直径（最近偶数）。

    根据环境母图的短边和配置比例计算直径，并舍入到最近的偶数。

    Args:
        environment_width: 环境母图宽度
        environment_height: 环境母图高度
        short_edge_ratio: 短边比例（默认 0.14）

    Returns:
        int: 直径（偶数像素）
    """
    short_edge = min(environment_width, environment_height)
    diameter_float = short_edge * short_edge_ratio

    # 舍入到最近偶数
    diameter_rounded = round(diameter_float)
    if diameter_rounded % 2 != 0:
        # 奇数，取最近的偶数（向上或向下）
        # 使用标准的"最近偶数"规则：0.5 时向下取偶
        if diameter_float - math.floor(diameter_float) < 0.5:
            diameter_px = diameter_rounded - 1
        else:
            diameter_px = diameter_rounded + 1
    else:
        diameter_px = diameter_rounded

    return diameter_px
