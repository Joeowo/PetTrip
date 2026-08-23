from __future__ import annotations

import asyncio
import json

import httpx

from agent_service.chat_provider import ChatMessage, OpenAICompatibleChatProvider
from agent_service.structured_output import StructuredOutputRegistry


def test_openai_compatible_provider_sends_versioned_schema_with_json_object(monkeypatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "type": "scene_draft",
                                    "schema_version": "0.1",
                                    "title": "潮汐灯塔",
                                    "theme": "seaside",
                                    "summary": "海边目的地。",
                                    "landmark_kind": "lighthouse",
                                }
                            )
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    provider = OpenAICompatibleChatProvider(
        base_url="https://chat.example/v1",
        api_key="secret-provider-key",
        model="structured-model",
        timeout_seconds=1,
        temperature=0,
        max_tokens=256,
    )
    schema_request = StructuredOutputRegistry().request_for(
        schema_name="scene_draft", schema_version="0.1"
    )

    result = asyncio.run(
        provider.complete_structured(
            [ChatMessage(role="user", content="生成海边场景草案")], schema_request
        )
    )

    assert json.loads(result)["title"] == "潮汐灯塔"
    assert captured["response_format"] == {"type": "json_object"}
    schema_instruction = captured["messages"][0]
    assert schema_instruction["role"] == "system"
    assert "scene_draft" in schema_instruction["content"]
    assert '"schema_version":"0.1"' in schema_instruction["content"]
    assert '"title"' in schema_instruction["content"]
    assert captured["messages"][1] == {
        "role": "user",
        "content": "生成海边场景草案",
    }
