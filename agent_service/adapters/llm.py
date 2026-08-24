"""OpenAI 兼容 Chat Completions Provider。"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from jsonschema import Draft202012Validator

from ..shared.structured_output import StructuredOutputRequest


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
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url.endswith("/v1"):
            normalized_base_url += "/v1"
        self._base_url = normalized_base_url
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
        encoded_messages = [instruction, *messages]
        # The configured gateway accepts plain text completions but rejects
        # response_format=json_object. Ask for JSON in the prompt and fail
        # closed locally before any caller can persist the result.
        last_error: ChatProviderError | None = None
        for _ in range(2):
            try:
                raw = await self._complete(encoded_messages)
                parsed = json.loads(raw)
                errors = list(Draft202012Validator(request.json_schema).iter_errors(parsed))
                if errors:
                    last_error = ChatProviderError("结构化 Chat 输出未通过 JSON Schema 校验。")
                    continue
                return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            except json.JSONDecodeError:
                last_error = ChatProviderError("结构化 Chat 输出不是合法 JSON。")
            except ChatProviderError as exc:
                last_error = exc
        raise last_error or ChatProviderError("结构化 Chat 输出失败。")

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
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise ChatProviderError(
                f"Chat Provider HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ChatProviderError(f"Chat Provider 请求失败: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatProviderError("Chat Provider 返回无效文本响应。") from exc
        if not isinstance(content, str) or not content.strip():
            raise ChatProviderError("Chat Provider 返回空文本响应。")
        return content.strip()
