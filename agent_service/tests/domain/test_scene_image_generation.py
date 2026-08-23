"""测试场景生成的真实 Provider 集成（T7）。"""

import base64
import hashlib
import json
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from agent_service.adapters.image import ImageEditRequest, ImageResult
from agent_service.domain.scene_image_generation import (
    build_scene_generation_prompt,
    generate_final_scene_with_provider,
    SceneGenerationInput,
)
from agent_service.shared.config import Settings


def test_build_scene_generation_prompt():
    """测试：提示词构建包含宠物行为和情绪。"""
    prompt = build_scene_generation_prompt("四处张望", "好奇")

    assert "四处张望" in prompt
    assert "好奇" in prompt
    assert "pet" in prompt.lower()
    assert "circle" in prompt.lower()


@pytest.mark.asyncio
async def test_generate_final_scene_with_provider_success():
    """测试：成功调用 Provider 生成最终场景。"""
    # 创建测试输入
    aperture_img = Image.new("RGB", (2048, 1152), color=(100, 150, 200))
    aperture_buffer = BytesIO()
    aperture_img.save(aperture_buffer, format="PNG")
    aperture_bytes = aperture_buffer.getvalue()

    mask_img = Image.new("L", (2048, 1152), color=255)
    mask_buffer = BytesIO()
    mask_img.save(mask_buffer, format="PNG")
    mask_bytes = mask_buffer.getvalue()

    input_data: SceneGenerationInput = {
        "aperture_bytes": aperture_bytes,
        "mask_bytes": mask_bytes,
        "pet_behavior": "四处张望",
        "pet_emotion": "好奇",
        "size": "2048x1152",
    }

    # 创建 mock 响应图像
    result_img = Image.new("RGB", (2048, 1152), color=(200, 100, 50))
    result_buffer = BytesIO()
    result_img.save(result_buffer, format="PNG")
    result_bytes = result_buffer.getvalue()

    # Mock Provider
    mock_result = ImageResult(
        data=result_bytes,
        mime_type="image/png",
        width=2048,
        height=1152,
    )

    # 创建最小测试配置（使用 Mock 对象）
    from unittest.mock import Mock
    config = Mock(spec=Settings)
    config.image_base_url = "https://api.example.com"
    config.image_api_key = "test-key"
    config.image_model = "gpt-image-2"
    config.image_timeout = 120.0
    config.image_max_decoded_bytes = 20 * 1024 * 1024
    config.image_max_pixels = 20000000

    # Mock edit 方法
    with patch(
        "agent_service.adapters.image.OpenAICompatibleImageProvider.edit",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_edit:
        result = await generate_final_scene_with_provider(config, input_data)

        # 验证调用
        assert mock_edit.called
        call_args = mock_edit.call_args[0][0]
        assert isinstance(call_args, ImageEditRequest)
        assert call_args.image == aperture_bytes
        assert call_args.mask == mask_bytes
        assert "四处张望" in call_args.prompt
        assert "好奇" in call_args.prompt
        assert call_args.size == "2048x1152"

        # 验证结果
        assert result.data == result_bytes
        assert result.mime_type == "image/png"
        assert result.width == 2048
        assert result.height == 1152


@pytest.mark.asyncio
async def test_generate_final_scene_with_provider_http_error():
    """测试：Provider HTTP 错误处理。"""
    aperture_img = Image.new("RGB", (1024, 1024), color=(100, 150, 200))
    aperture_buffer = BytesIO()
    aperture_img.save(aperture_buffer, format="PNG")
    aperture_bytes = aperture_buffer.getvalue()

    mask_img = Image.new("L", (1024, 1024), color=255)
    mask_buffer = BytesIO()
    mask_img.save(mask_buffer, format="PNG")
    mask_bytes = mask_buffer.getvalue()

    input_data: SceneGenerationInput = {
        "aperture_bytes": aperture_bytes,
        "mask_bytes": mask_bytes,
        "pet_behavior": "站立",
        "pet_emotion": "警觉",
        "size": "1024x1024",
    }

    config = Config(
        chat_base_url="https://api.example.com",
        chat_api_key="test-key",
        chat_model="gpt-4",
        chat_timeout=30.0,
        image_base_url="https://api.example.com",
        image_api_key="test-key",
        image_model="gpt-image-2",
        image_timeout=120.0,
        image_request_size="1024x1024",
        image_generation_path="/images/generations",
        image_canvas_width=1024,
        image_canvas_height=1024,
        image_max_decoded_bytes=20 * 1024 * 1024,
        image_max_pixels=20000000,
        max_image_dimension=4096,
        max_image_pixels=20000000,
    )

    from agent_service.adapters.image import ImageProviderError

    # Mock edit 方法抛出错误
    with patch(
        "agent_service.adapters.image.OpenAICompatibleImageProvider.edit",
        new_callable=AsyncMock,
        side_effect=ImageProviderError("图片编辑服务暂时不可用。"),
    ):
        with pytest.raises(ImageProviderError, match="图片编辑服务暂时不可用"):
            await generate_final_scene_with_provider(config, input_data)
