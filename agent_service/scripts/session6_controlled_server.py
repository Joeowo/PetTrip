"""会话 6 跨进程恢复验收使用的受控 Provider 服务入口。"""

from __future__ import annotations

import asyncio
import io
import json
import os
import socket
from pathlib import Path
from typing import Any

import uvicorn
from PIL import Image

from agent_service.app import create_app
from agent_service.chat_provider import ChatMessage
from agent_service.config import load_settings
from agent_service.image_provider import (
    ImageGenerationRequest,
    ImageGenerationProvider,
    ImageResult,
)


CASE_IDS = {
    "第一轮：请根据参考图记录海边灯塔场景。": "round1_structured",
    "请只用简短自然语言，基于上面的已校验结构化结果回答最初请求；不要返回 JSON。": "round1_text",
    "第二轮：请基于上一轮场景补充宠物散步建议。": "round2",
    "执行中重启：这一轮不得自动重试。": "interrupted",
    "重启后继续：请确认会话仍可创建新 Run。": "after_restart",
}


class ControlledChatProvider:
    """返回确定性文本，并记录每次 Provider 调用。"""

    def __init__(self, *, delay_seconds: float, call_log: Path) -> None:
        self._delay_seconds = delay_seconds
        self._call_log = call_log

    def _record(self, kind: str, messages: list[ChatMessage]) -> None:
        self._call_log.parent.mkdir(parents=True, exist_ok=True)
        last_user = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        with self._call_log.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"kind": kind, "case_id": CASE_IDS.get(last_user, "unknown")},
                    ensure_ascii=False,
                )
                + "\n"
            )

    async def complete(self, messages: list[ChatMessage]) -> str:
        self._record("chat.complete", messages)
        await asyncio.sleep(self._delay_seconds)
        last_user = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        return f"受控回复：{last_user}"

    async def complete_structured(
        self, messages: list[ChatMessage], request: object
    ) -> str:
        self._record("chat.complete_structured", messages)
        await asyncio.sleep(self._delay_seconds)
        return json.dumps(
            {
                "type": "scene_draft",
                "schema_version": "0.1",
                "title": "受控场景",
                "theme": "seaside",
                "summary": "会话 6 受控结构化结果。",
                "landmark_kind": "lighthouse",
            },
            ensure_ascii=False,
        )


class ControlledImageProvider(ImageGenerationProvider):
    """返回固定 PNG，验证图片目录恢复而不调用外部 Provider。"""

    def __init__(self, *, call_log: Path) -> None:
        self._call_log = call_log

    async def generate(self, request: ImageGenerationRequest) -> ImageResult:
        self._call_log.parent.mkdir(parents=True, exist_ok=True)
        with self._call_log.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "kind": "image.generate",
                        "case_id": "round1_image"
                        if request.prompt in CASE_IDS
                        else "unknown",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        buffer = io.BytesIO()
        Image.new("RGB", (80, 60), color=(30, 140, 220)).save(buffer, format="PNG")
        return ImageResult(
            data=buffer.getvalue(),
            mime_type="image/png",
            width=80,
            height=60,
        )


def main() -> None:
    settings = load_settings()
    call_log = Path(os.environ.get("SESSION6_PROVIDER_CALL_LOG", "provider-calls.log"))
    delay_seconds = float(os.environ.get("SESSION6_PROVIDER_DELAY_SECONDS", "0"))
    port_file = Path(os.environ["SESSION6_PORT_FILE"])
    app = create_app(
        settings=settings,
        provider=ControlledChatProvider(
            delay_seconds=delay_seconds,
            call_log=call_log,
        ),
        image_provider=ControlledImageProvider(call_log=call_log),
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((settings.host, 0))
    listener.listen(2048)
    listener.set_inheritable(True)
    temporary_port_file = port_file.with_suffix(".tmp")
    temporary_port_file.write_text(str(listener.getsockname()[1]), encoding="ascii")
    os.replace(temporary_port_file, port_file)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=0,
        reload=False,
        workers=1,
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run(sockets=[listener])


if __name__ == "__main__":
    main()
