"""场景生成的图片生成 Provider 调用封装（T7）。"""

import asyncio
import hashlib
from typing import TypedDict

from agent_service.adapters.image import (
    ImageEditRequest,
    ImageResult,
    OpenAICompatibleImageProvider,
    ImageProviderError,
)
from agent_service.adapters.async_image_task import (
    AsyncImageTaskClient,
    AsyncImageTaskRequest,
)
from agent_service.shared.config import Settings


class SceneGenerationInput(TypedDict):
    """场景生成输入。"""
    aperture_bytes: bytes
    mask_bytes: bytes
    pet_behavior: str
    pet_emotion: str
    size: str  # 例如 "2048x1152"
    idempotency_key: str  # 幂等键（可选）


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


def generate_idempotency_key(
    scene_id: str,
    aperture_sha256: str,
    pet_behavior: str,
    pet_emotion: str,
) -> str:
    """生成幂等键。

    基于场景 ID、aperture 哈希和宠物描述生成唯一幂等键。
    相同输入总是生成相同的幂等键，确保重试不重复扣费。

    Args:
        scene_id: 场景 ID
        aperture_sha256: aperture 图像的 SHA256 哈希
        pet_behavior: 宠物行为
        pet_emotion: 宠物情绪

    Returns:
        str: 幂等键
    """
    content = f"{scene_id}:{aperture_sha256}:{pet_behavior}:{pet_emotion}"
    hash_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"scene-{scene_id[:8]}-{hash_digest[:16]}"


async def generate_final_scene_with_provider(
    config: Settings,
    input_data: SceneGenerationInput,
) -> ImageResult:
    """使用真实 Provider 生成最终场景（异步版本）。

    优先使用异步任务 API（更可靠），回退到同步 edit API。

    Args:
        config: 配置对象
        input_data: 场景生成输入（aperture、mask、宠物描述）

    Returns:
        ImageResult: 生成的最终场景图像

    Raises:
        ImageProviderError: 图片生成失败
    """
    # 构建提示词
    prompt = build_scene_generation_prompt(
        input_data["pet_behavior"],
        input_data["pet_emotion"],
    )

    # 尝试使用异步任务 API
    if hasattr(config, "image_use_async_tasks") and config.image_use_async_tasks:
        client = AsyncImageTaskClient(
            base_url=config.image_base_url,
            api_key=config.image_api_key,
            timeout=config.image_timeout,
        )

        request = AsyncImageTaskRequest(
            model=config.image_model,
            prompt=prompt,
            image_bytes=input_data["aperture_bytes"],
            mask_bytes=input_data["mask_bytes"],
            size=input_data["size"],
            quality="high",
            n=1,
            idempotency_key=input_data.get("idempotency_key", ""),
        )

        result = client.submit_and_wait(request)
        return result

    # 回退到同步 edit API
    provider = OpenAICompatibleImageProvider(
        base_url=config.image_base_url,
        api_key=config.image_api_key,
        model=config.image_model,
        timeout_seconds=config.image_timeout,
        request_size=input_data["size"],
        max_decoded_bytes=config.image_max_decoded_bytes,
        max_image_pixels=config.image_max_pixels,
    )

    edit_request = ImageEditRequest(
        image=input_data["aperture_bytes"],
        mask=input_data["mask_bytes"],
        prompt=prompt,
        size=input_data["size"],
    )

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
