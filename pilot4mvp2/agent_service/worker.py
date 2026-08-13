"""单进程异步 Run Worker。"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from .chat_provider import ChatMessage, ChatModelProvider, ChatProviderError
from .errors import CHAT_PROVIDER_UNAVAILABLE
from .ids import new_id
from .storage import Storage

LOGGER = logging.getLogger("uvicorn.error")


class RunWorker:
    """轮询 queued Run，按会话历史调用文本模型。"""

    def __init__(
        self,
        *,
        storage: Storage,
        provider: ChatModelProvider,
        poll_interval: float,
    ) -> None:
        self._storage = storage
        self._provider = provider
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="pettrip-run-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def process_one(self) -> bool:
        """领取并执行一个 Run，方便测试与单次调度。"""
        run = self._storage.claim_next_queued_run()
        if run is None:
            return False

        request_id = new_id("req")
        try:
            messages = [
                ChatMessage(role=row["role"], content=row["content_text"] or "")
                for row in self._storage.list_messages_for_run(
                    run["session_id"], run["api_client_id"], run["id"]
                )
            ]
            assistant_text = await self._provider.complete(messages)
        except ChatProviderError:
            LOGGER.warning(
                "provider_failed request_id=%s run_id=%s error_type=ChatProviderError",
                request_id,
                run["id"],
            )
            self._storage.mark_run_failed(
                run["id"],
                error_code=CHAT_PROVIDER_UNAVAILABLE,
                error_message="文本模型服务暂时不可用。",
            )
        except Exception as exc:
            LOGGER.error(
                "provider_failed request_id=%s run_id=%s error_type=%s",
                request_id,
                run["id"],
                type(exc).__name__,
            )
            self._storage.mark_run_failed(
                run["id"],
                error_code=CHAT_PROVIDER_UNAVAILABLE,
                error_message="文本模型服务暂时不可用。",
            )
        else:
            self._storage.complete_run_success(run["id"], assistant_text=assistant_text)
        return True

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                processed = await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error(
                    "worker_iteration_failed request_id=%s error_type=%s",
                    new_id("req"),
                    type(exc).__name__,
                )
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self._poll_interval
                    )
                except TimeoutError:
                    continue
