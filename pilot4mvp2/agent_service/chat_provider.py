"""OpenAI 兼容 Chat Completions Provider。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class ChatProviderError(RuntimeError):
    """Provider 网络、状态或响应格式不符合约定。"""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class ChatModelProvider(Protocol):
    """隔离外部 Chat Provider 的最小接口。"""

    async def complete(self, messages: list[ChatMessage]) -> str: ...


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

    async def complete(self, messages: list[ChatMessage]) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
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
