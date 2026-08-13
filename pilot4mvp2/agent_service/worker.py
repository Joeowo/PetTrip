"""单进程异步 Run Worker。"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from .chat_provider import ChatMessage, ChatModelProvider, ChatProviderError, VisionImage
from .errors import CHAT_PROVIDER_UNAVAILABLE, IMAGE_PROVIDER_UNAVAILABLE, INTERNAL_ERROR
from .file_storage import LocalFileIntegrityError, LocalImageStorage
from .ids import new_id
from .image_provider import (
    ImageGenerationProvider,
    ImageGenerationRequest,
    ImageProviderError,
)
from .storage import Storage

LOGGER = logging.getLogger("uvicorn.error")


class RunWorker:
    """轮询 queued Run，执行文本和/或图片输出。"""

    def __init__(
        self,
        *,
        storage: Storage,
        provider: ChatModelProvider,
        poll_interval: float,
        file_storage: LocalImageStorage | None = None,
        image_provider: ImageGenerationProvider | None = None,
        image_canvas_width: int = 1024,
        image_canvas_height: int = 1024,
        image_max_pixels: int = 20_000_000,
    ) -> None:
        self._storage = storage
        self._file_storage = file_storage
        self._provider = provider
        self._image_provider = image_provider
        self._image_canvas_width = image_canvas_width
        self._image_canvas_height = image_canvas_height
        self._image_max_pixels = image_max_pixels
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
        modalities = json.loads(run["response_format"]).get("modalities", [])
        wants_text = "text" in modalities
        wants_image = "image" in modalities
        assistant_text: str | None = None
        output_file: dict[str, object] | None = None
        generated_rel_path: str | None = None
        try:
            if wants_text:
                messages = await self._build_chat_messages(run)
                assistant_text = await self._provider.complete(messages)

            if wants_image:
                if self._image_provider is None or self._file_storage is None:
                    raise ImageProviderError("图片生成服务暂时不可用。")
                self._storage.add_event(
                    run["id"], "image_generation.started", payload=None
                )
                request_input = json.loads(run["request_input"])
                image_result = await self._image_provider.generate(
                    ImageGenerationRequest(prompt=request_input["text"])
                )
                file_id = new_id("file")
                try:
                    stored = await asyncio.to_thread(
                        self._file_storage.normalize_and_store_generated,
                        file_id=file_id,
                        data=image_result.data,
                        target_width=self._image_canvas_width,
                        target_height=self._image_canvas_height,
                        max_pixels=self._image_max_pixels,
                    )
                except LocalFileIntegrityError as exc:
                    raise ImageProviderError("图片生成服务返回无效图片。") from exc
                generated_rel_path = stored.rel_path
                output_file = {
                    "id": file_id,
                    "mime_type": stored.mime_type,
                    "size_bytes": stored.size_bytes,
                    "sha256": stored.sha256,
                    "width": stored.width,
                    "height": stored.height,
                    "rel_path": stored.rel_path,
                }

            self._storage.complete_run_success(
                run["id"],
                assistant_text=assistant_text,
                output_file=output_file,
            )
        except LocalFileIntegrityError:
            LOGGER.error(
                "local_file_failed request_id=%s run_id=%s error_type=LocalFileIntegrityError",
                request_id,
                run["id"],
            )
            self._delete_generated(generated_rel_path)
            self._storage.mark_run_failed(
                run["id"],
                error_code=INTERNAL_ERROR,
                error_message="输入图片不可用，请重新上传。",
            )
        except ChatProviderError:
            LOGGER.warning(
                "provider_failed request_id=%s run_id=%s provider=chat",
                request_id,
                run["id"],
            )
            self._delete_generated(generated_rel_path)
            self._storage.mark_run_failed(
                run["id"],
                error_code=CHAT_PROVIDER_UNAVAILABLE,
                error_message="文本模型服务暂时不可用。",
            )
        except ImageProviderError:
            LOGGER.warning(
                "provider_failed request_id=%s run_id=%s provider=image",
                request_id,
                run["id"],
            )
            self._delete_generated(generated_rel_path)
            self._storage.mark_run_failed(
                run["id"],
                error_code=IMAGE_PROVIDER_UNAVAILABLE,
                error_message="图片生成服务暂时不可用。",
            )
        except Exception as exc:
            LOGGER.error(
                "run_failed request_id=%s run_id=%s error_type=%s",
                request_id,
                run["id"],
                type(exc).__name__,
            )
            self._delete_generated(generated_rel_path)
            error_code = IMAGE_PROVIDER_UNAVAILABLE if wants_image else CHAT_PROVIDER_UNAVAILABLE
            error_message = (
                "图片生成服务暂时不可用。"
                if wants_image
                else "文本模型服务暂时不可用。"
            )
            self._storage.mark_run_failed(
                run["id"], error_code=error_code, error_message=error_message
            )
        return True

    async def _build_chat_messages(self, run: dict[str, object]) -> list[ChatMessage]:
        if self._file_storage is None:
            file_storage = None
        else:
            file_storage = self._file_storage
        messages: list[ChatMessage] = []
        for row in self._storage.list_messages_for_run(
            str(run["session_id"]), str(run["api_client_id"]), str(run["id"])
        ):
            images: tuple[VisionImage, ...] = ()
            if row["run_id"] == run["id"] and row["role"] == "user":
                file_rows = self._storage.list_input_files_for_message(
                    row["id"], str(run["api_client_id"])
                )
                if file_rows and file_storage is None:
                    raise LocalFileIntegrityError("缺少本地图片存储。")
                loaded_images: list[VisionImage] = []
                for file_row in file_rows:
                    data = await asyncio.to_thread(
                        file_storage.read_verified,  # type: ignore[union-attr]
                        file_row,
                    )
                    loaded_images.append(
                        VisionImage(mime_type=file_row["mime_type"], data=data)
                    )
                images = tuple(loaded_images)
            messages.append(
                ChatMessage(
                    role=row["role"],
                    content=row["content_text"] or "",
                    images=images,
                )
            )
        return messages

    def _delete_generated(self, rel_path: str | None) -> None:
        if rel_path is not None and self._file_storage is not None:
            try:
                self._file_storage.delete(rel_path)
            except (OSError, ValueError):
                LOGGER.error("generated_file_cleanup_failed error_type=storage")

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
