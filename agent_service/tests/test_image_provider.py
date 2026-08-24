from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from agent_service.adapters.image import (
    ImageGenerationRequest,
    ImageProviderError,
    ImageReference,
    OpenAICompatibleImageProvider,
)


def _image_b64(size: tuple[int, int] = (1402, 1122)) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(255, 0, 0)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_image_provider_posts_generation_request_to_images_endpoint() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": _image_b64()}]})

    provider = OpenAICompatibleImageProvider(
        base_url="https://image.example/v1",
        api_key="provider-secret",
        model="gpt-image-2",
        timeout_seconds=3,
        request_size="1024x1024",
        max_decoded_bytes=2_000_000,
        max_image_pixels=5_000_000,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.generate(ImageGenerationRequest(prompt="一只海边小狗"))

    assert result.width == 1402
    assert result.height == 1122
    assert requests[0].url == "https://image.example/v1/images/generations"
    assert requests[0].headers["authorization"] == "Bearer provider-secret"
    payload = json.loads(requests[0].content)
    assert payload == {
        "model": "gpt-image-2",
        "prompt": "一只海边小狗",
        "size": "1024x1024",
        "n": 1,
        "response_format": "b64_json",
    }


@pytest.mark.asyncio
async def test_image_provider_honors_request_specific_size() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": _image_b64((2048, 1152))}]})

    provider = OpenAICompatibleImageProvider(
        base_url="https://image.example/v1",
        api_key="provider-secret",
        model="gpt-image-2",
        timeout_seconds=3,
        request_size="1024x1024",
        max_decoded_bytes=4_000_000,
        max_image_pixels=5_000_000,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.generate(
        ImageGenerationRequest(prompt="定位图", size="2048x1152")
    )

    assert (result.width, result.height) == (2048, 1152)
    assert json.loads(requests[0].content)["size"] == "2048x1152"


@pytest.mark.asyncio
async def test_image_provider_posts_role_ordered_reference_metadata() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": _image_b64()}]})

    provider = OpenAICompatibleImageProvider(
        base_url="https://image.example/v1",
        api_key="provider-secret",
        model="gpt-image-2",
        timeout_seconds=3,
        request_size="1024x1024",
        max_decoded_bytes=2_000_000,
        max_image_pixels=5_000_000,
        transport=httpx.MockTransport(handler),
    )
    references = [
        ImageReference(
            role="composition_reference",
            file_id="file-composition",
            mime_type="image/png",
            width=2309,
            height=1080,
            sha256="c" * 64,
            data=b"composition",
            order_index=1,
        ),
        ImageReference(
            role="style_reference",
            file_id="file-style",
            mime_type="image/png",
            width=640,
            height=480,
            sha256="s" * 64,
            data=b"style",
            order_index=0,
        ),
    ]

    await provider.generate(
        ImageGenerationRequest(prompt="环境 Prompt", references=tuple(references))
    )

    payload = json.loads(requests[0].content)
    assert payload["prompt"] == "环境 Prompt"
    assert payload["references"] == [
        {
            "role": "style_reference",
            "file_id": "file-style",
            "mime_type": "image/png",
            "width": 640,
            "height": 480,
            "sha256": "s" * 64,
            "data": "c3R5bGU=",
            "order_index": 0,
        },
        {
            "role": "composition_reference",
            "file_id": "file-composition",
            "mime_type": "image/png",
            "width": 2309,
            "height": 1080,
            "sha256": "c" * 64,
            "data": "Y29tcG9zaXRpb24=",
            "order_index": 1,
        },
    ]


def test_image_requests_reject_duplicate_reference_order_indexes() -> None:
    references = tuple(
        ImageReference(
            role=role,
            file_id=file_id,
            mime_type="image/png",
            width=1,
            height=1,
            sha256=character * 64,
            data=b"data",
            order_index=0,
        )
        for role, file_id, character in (
            ("style_reference", "style", "a"),
            ("composition_reference", "composition", "b"),
        )
    )

    with pytest.raises(ValueError, match="order_index 必须唯一"):
        ImageGenerationRequest(prompt="test", references=references)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"data": [{"b64_json": "not-base64"}]},
        {"data": [{}]},
        {"data": []},
    ],
)
async def test_image_provider_rejects_invalid_base64_responses(response: dict) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    provider = OpenAICompatibleImageProvider(
        base_url="https://image.example/v1",
        api_key="secret",
        model="gpt-image-2",
        timeout_seconds=3,
        request_size="1024x1024",
        max_decoded_bytes=2_000_000,
        max_image_pixels=5_000_000,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ImageProviderError):
        await provider.generate(ImageGenerationRequest(prompt="test"))


@pytest.mark.asyncio
async def test_image_provider_maps_http_failure_without_leaking_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="provider key=secret and /private/path")

    provider = OpenAICompatibleImageProvider(
        base_url="https://image.example/v1",
        api_key="secret",
        model="gpt-image-2",
        timeout_seconds=3,
        request_size="1024x1024",
        max_decoded_bytes=2_000_000,
        max_image_pixels=5_000_000,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ImageProviderError) as exc_info:
        await provider.generate(ImageGenerationRequest(prompt="test"))
    assert "secret" not in str(exc_info.value)
    assert "/private/path" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_image_provider_wrong_endpoint_is_unavailable() -> None:
    requested_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(404, json={"error": "not found"})

    provider = OpenAICompatibleImageProvider(
        base_url="https://image.example/v1",
        api_key="secret",
        model="gpt-image-2",
        timeout_seconds=3,
        request_size="1024x1024",
        max_decoded_bytes=2_000_000,
        max_image_pixels=5_000_000,
        generation_path="/wrong-endpoint",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ImageProviderError):
        await provider.generate(ImageGenerationRequest(prompt="test"))
    assert requested_paths == ["/v1/wrong-endpoint"]
