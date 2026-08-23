"""OpenAI-compatible image generation Provider."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
from dataclasses import dataclass
from typing import Any

import httpx
from PIL import Image, UnidentifiedImageError


class ImageProviderError(RuntimeError):
    """Provider request or image response violated the private contract."""


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str


@dataclass(frozen=True)
class ImageEditRequest:
    """图像编辑请求（Inpainting）。

    用于场景生成：将 aperture 图中的黑色圆替换为宠物。
    """
    image: bytes  # 原始图像（PNG）
    mask: bytes   # Mask 图像（PNG，白色区域将被编辑）
    prompt: str   # 编辑提示词
    size: str = "1024x1024"  # 期望输出尺寸


@dataclass(frozen=True)
class ImageResult:
    data: bytes
    mime_type: str
    width: int
    height: int


class ImageGenerationProvider:
    """Protocol-shaped base class for dependency injection and fakes."""

    async def generate(self, request: ImageGenerationRequest) -> ImageResult:
        raise NotImplementedError

    async def edit(self, request: ImageEditRequest) -> ImageResult:
        raise NotImplementedError


class OpenAICompatibleImageProvider(ImageGenerationProvider):
    """Call the Images Generations endpoint and validate its Base64 image."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        request_size: str,
        max_decoded_bytes: int,
        max_image_pixels: int,
        transport: httpx.AsyncBaseTransport | None = None,
        generation_path: str = "/images/generations",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._request_size = request_size
        self._max_decoded_bytes = max_decoded_bytes
        self._max_image_pixels = max_image_pixels
        self._transport = transport
        self._generation_path = "/" + generation_path.strip("/")

    async def generate(self, request: ImageGenerationRequest) -> ImageResult:
        payload = {
            "model": self._model,
            "prompt": request.prompt,
            "size": self._request_size,
            "n": 1,
            "response_format": "b64_json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}{self._generation_path}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                body: Any = response.json()
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise ImageProviderError("图片生成服务暂时不可用。") from exc

        try:
            encoded = body["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ImageProviderError("图片生成服务返回无效图片。") from exc
        if not isinstance(encoded, str) or not encoded:
            raise ImageProviderError("图片生成服务返回无效图片。")

        try:
            decoded = await asyncio.to_thread(self._decode_and_validate, encoded)
        except ImageProviderError:
            raise
        except Exception as exc:
            raise ImageProviderError("图片生成服务返回无效图片。") from exc
        return decoded

    def _decode_and_validate(self, encoded: str) -> ImageResult:
        if len(encoded) > ((self._max_decoded_bytes + 2) // 3) * 4:
            raise ImageProviderError("图片生成服务返回的图片过大。")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageProviderError("图片生成服务返回无效图片。") from exc
        if len(data) > self._max_decoded_bytes:
            raise ImageProviderError("图片生成服务返回的图片过大。")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image_format = image.format or ""
                width, height = image.size
                if width * height > self._max_image_pixels:
                    raise ImageProviderError("图片生成服务返回的图片过大。")
                image.load()
        except ImageProviderError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImageProviderError("图片生成服务返回无效图片。") from exc

        mime_type = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "WEBP": "image/webp",
        }.get(image_format)
        if mime_type is None:
            raise ImageProviderError("图片生成服务返回不支持的图片格式。")
        return ImageResult(
            data=data,
            mime_type=mime_type,
            width=width,
            height=height,
        )

    async def edit(self, request: ImageEditRequest) -> ImageResult:
        """调用图像编辑接口（Inpainting）。

        Args:
            request: 图像编辑请求，包含原图、Mask 和提示词

        Returns:
            ImageResult: 编辑后的图像

        Raises:
            ImageProviderError: 服务不可用或返回无效图片
        """
        # 构建 multipart/form-data 请求
        files = {
            "image": ("image.png", io.BytesIO(request.image), "image/png"),
            "mask": ("mask.png", io.BytesIO(request.mask), "image/png"),
        }

        data = {
            "model": self._model,
            "prompt": request.prompt,
            "n": 1,
            "size": request.size,
            "response_format": "b64_json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/images/edits",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    files=files,
                    data=data,
                )
                response.raise_for_status()
                body: Any = response.json()
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise ImageProviderError("图片编辑服务暂时不可用。") from exc

        try:
            encoded = body["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ImageProviderError("图片编辑服务返回无效图片。") from exc
        if not isinstance(encoded, str) or not encoded:
            raise ImageProviderError("图片编辑服务返回无效图片。")

        try:
            decoded = await asyncio.to_thread(self._decode_and_validate, encoded)
        except ImageProviderError:
            raise
        except Exception as exc:
            raise ImageProviderError("图片编辑服务返回无效图片。") from exc
        return decoded
