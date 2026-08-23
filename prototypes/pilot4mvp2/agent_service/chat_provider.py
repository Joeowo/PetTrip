"""OpenAI 兼容 Chat Completions Provider。"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .structured_output import StructuredOutputRequest


class ChatProviderError(RuntimeError):
    """Provider 网络、状态或响应格式不符合约定。"""


@dataclass(frozen=True)
class VisionImage:
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    images: tuple[VisionImage, ...] = ()


class ChatModelProvider(Protocol):
    """隔离外部 Chat Provider 的文本与结构化完成接口。"""

    async def complete(self, messages: list[ChatMessage]) -> str: ...

    async def complete_structured(
        self, messages: list[ChatMessage], request: StructuredOutputRequest
    ) -> str: ...


class OpenAICompatibleChatProvider:
    """调用 OpenAI compatible `/chat/completions`，只返回助手纯文本。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        temperature: float,
        max_tokens: int,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._max_tokens = max_tokens

    @staticmethod
    def _message_payload(message: ChatMessage) -> dict[str, Any]:
        if not message.images:
            return {"role": message.role, "content": message.content}
        content: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        for image in message.images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.mime_type};base64,{encoded}",
                    },
                }
            )
        return {"role": message.role, "content": content}

    async def complete(self, messages: list[ChatMessage]) -> str:
        return await self._complete(messages)

    async def complete_structured(
        self, messages: list[ChatMessage], request: StructuredOutputRequest
    ) -> str:
        schema_json = await asyncio.to_thread(
            json.dumps,
            request.json_schema,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        instruction = ChatMessage(
            role="system",
            content=(
                "只返回一个 JSON 对象，不要返回 Markdown 或说明文字。"
                f"schema_name={request.schema_name};"
                f'\"schema_version\":\"{request.schema_version}\";'
                f"对象必须严格符合此 JSON Schema：{schema_json}"
            ),
        )
        return await self._complete(
            [instruction, *messages], response_format={"type": "json_object"}
        )

    async def _complete(
        self,
        messages: list[ChatMessage],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        encoded_messages = await asyncio.to_thread(
            lambda: [self._message_payload(message) for message in messages]
        )
        payload = {
            "model": self._model,
            "messages": encoded_messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                body: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ChatProviderError("Chat Provider 请求失败。") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatProviderError("Chat Provider 返回无效文本响应。") from exc
        if not isinstance(content, str) or not content.strip():
            raise ChatProviderError("Chat Provider 返回空文本响应。")
        return content.strip()
