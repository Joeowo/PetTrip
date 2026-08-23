"""场景生成的图片生成 Provider 调用封装（T7）。"""

import asyncio
from typing import TypedDict

from agent_service.adapters.image import (
    ImageEditRequest,
    ImageResult,
    OpenAICompatibleImageProvider,
    ImageProviderError,
)
from agent_service.shared.config import Settings


class SceneGenerationInput(TypedDict):
    """场景生成输入。"""
    aperture_bytes: bytes
    mask_bytes: bytes
    pet_behavior: str
    pet_emotion: str
    size: str  # 例如 "2048x1152"


def build_scene_generation_prompt(pet_behavior: str, pet_emotion: str) -> str:
    """构建场景生成提示词。

    Args:
        pet_behavior: 宠物行为描述，例如 "四处张望"
        pet_emotion: 宠物情绪，例如 "好奇"

    Returns:
        str: 完整的提示词
    """
    return (
        f"Replace the black circle with a cute pet character. "
        f"The pet should be {pet_behavior}, showing {pet_emotion} emotion. "
        f"Keep the surrounding environment unchanged. "
        f"The pet should fit naturally within the circular area."
    )


async def generate_final_scene_with_provider(
    config: Settings,
    input_data: SceneGenerationInput,
) -> ImageResult:
    """使用真实 Provider 生成最终场景。

    Args:
        config: 配置对象
        input_data: 场景生成输入（aperture、mask、宠物描述）

    Returns:
        ImageResult: 生成的最终场景图像

    Raises:
        ImageProviderError: 图片生成失败
    """
    # 创建 Provider
    provider = OpenAICompatibleImageProvider(
        base_url=config.image_base_url,
        api_key=config.image_api_key,
        model=config.image_model,
        timeout_seconds=config.image_timeout,
        request_size=input_data["size"],
        max_decoded_bytes=config.image_max_decoded_bytes,
        max_image_pixels=config.image_max_pixels,
    )

    # 构建提示词
    prompt = build_scene_generation_prompt(
        input_data["pet_behavior"],
        input_data["pet_emotion"],
    )

    # 创建编辑请求
    edit_request = ImageEditRequest(
        image=input_data["aperture_bytes"],
        mask=input_data["mask_bytes"],
        prompt=prompt,
        size=input_data["size"],
    )

    # 调用 Provider
    result = await provider.edit(edit_request)

    return result


def generate_final_scene_sync(
    config: Settings,
    input_data: SceneGenerationInput,
) -> bytes:
    """同步版本：生成最终场景。

    用于工作流集成（LangGraph 节点通常是同步的）。

    Args:
        config: 配置对象
        input_data: 场景生成输入

    Returns:
        bytes: 生成的最终场景图像（PNG 字节）

    Raises:
        ImageProviderError: 图片生成失败
    """
    result = asyncio.run(generate_final_scene_with_provider(config, input_data))
    return result.data
