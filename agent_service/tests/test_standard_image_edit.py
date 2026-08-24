from __future__ import annotations

import base64
import io

import httpx
import pytest
from PIL import Image

from agent_service.adapters.image import (
    ImageEditRequest,
    ImageReference,
    OpenAICompatibleImageProvider,
)


def _result_image() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color=(1, 2, 3)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_edit_sends_aperture_mask_and_pet_as_standard_multipart_parts() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": _result_image()}]})

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
    pet = ImageReference(
        role="pet_reference",
        file_id="pet/chongwu-bottom.png",
        mime_type="image/png",
        width=947,
        height=321,
        sha256="a" * 64,
        data=b"PET",
    )

    await provider.edit(
        ImageEditRequest(
            image=b"APERTURE",
            mask=b"MASK",
            prompt="Use image 2 as the canonical pet identity reference.",
            references=(pet,),
        )
    )

    body = requests[0].content.decode("utf-8", errors="replace")
    assert requests[0].url == "https://image.example/v1/images/edits"
    assert 'name="image[]"' in body
    assert body.index("APERTURE") < body.index("PET")
    assert 'name="mask"' in body
    assert "reference_0" not in body
    assert "reference_manifest" not in body
    assert "a" * 64 not in body
