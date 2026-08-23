from __future__ import annotations

import asyncio
import base64
import json

import httpx

from agent_service.chat_provider import (
    ChatMessage,
    OpenAICompatibleChatProvider,
    VisionImage,
)


def test_openai_compatible_provider_builds_vision_data_url(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "看到了蓝色方块。"}}]},
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    provider = OpenAICompatibleChatProvider(
        base_url="https://vision.example/v1",
        api_key="secret-provider-key",
        model="vision-model",
        timeout_seconds=1,
        temperature=0,
        max_tokens=32,
    )
    image_bytes = b"png-image-bytes"

    result = asyncio.run(
        provider.complete(
            [
                ChatMessage(role="assistant", content="历史文本"),
                ChatMessage(
                    role="user",
                    content="固定问题",
                    images=(VisionImage(mime_type="image/png", data=image_bytes),),
                ),
            ]
        )
    )

    assert result == "看到了蓝色方块。"
    assert captured["messages"][0] == {
        "role": "assistant",
        "content": "历史文本",
    }
    parts = captured["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "固定问题"}
    url = parts[1]["image_url"]["url"]
    prefix, encoded = url.split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded, validate=True) == image_bytes
