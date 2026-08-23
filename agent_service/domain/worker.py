"""单进程异步 Run Worker。"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from ..adapters.llm import ChatMessage, ChatModelProvider, ChatProviderError, VisionImage
from ..adapters.image import (
    ImageGenerationProvider,
    ImageGenerationRequest,
    ImageProviderError,
)
from ..shared.errors import (
    CHAT_PROVIDER_UNAVAILABLE,
    IMAGE_PROVIDER_UNAVAILABLE,
    INTERNAL_ERROR,
    STRUCTURED_OUTPUT_INVALID,
)
from ..shared.ids import new_id
from ..shared.structured_output import StructuredOutputInvalid, StructuredOutputRegistry
from ..storage.files import LocalFileIntegrityError, LocalImageStorage
from ..storage import Storage
from .destination_coordinator import DestinationCoordinatorService

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
        destination_coordinator: DestinationCoordinatorService | None = None,
    ) -> None:
        self._storage = storage
        self._file_storage = file_storage
        self._provider = provider
        self._image_provider = image_provider
        self._image_canvas_width = image_canvas_width
        self._image_canvas_height = image_canvas_height
        self._image_max_pixels = image_max_pixels
        self._structured_outputs = StructuredOutputRegistry()
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._destination_coordinator = destination_coordinator
        self._recovery_done = False

    def start(self) -> None:
        if self._task is None:
            self._stopping.clear()
            self._recovery_done = False
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
        response_format = json.loads(run["response_format"])
        modalities = response_format.get("modalities", [])
        wants_text = "text" in modalities
        wants_structured = "structured_data" in modalities
        wants_image = "image" in modalities
        assistant_text: str | None = None
        structured_data: dict[str, object] | None = None
        output_file: dict[str, object] | None = None
        generated_rel_path: str | None = None
        failure_stage = "internal"
        try:
            structured_request = None
            if wants_structured:
                structured_format = response_format.get("structured_output")
                if not isinstance(structured_format, dict):
                    raise StructuredOutputInvalid("缺少结构化输出 Schema。")
                structured_request = self._structured_outputs.request_for(
                    schema_name=structured_format.get("schema_name", ""),
                    schema_version=structured_format.get("schema_version", ""),
                )

            messages: list[ChatMessage] | None = None
            if wants_structured:
                messages = await self._build_chat_messages(run)
                failure_stage = "chat"
                raw_structured = await self._provider.complete_structured(
                    messages, structured_request
                )
                failure_stage = "internal"
                structured_data = self._structured_outputs.parse_and_validate(
                    raw_structured,
                    schema_name=structured_request.schema_name,
                    schema_version=structured_request.schema_version,
                )
                if wants_text:
                    structured_context = json.dumps(
                        structured_data,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    text_messages = [
                        *messages,
                        ChatMessage(role="assistant", content=structured_context),
                        ChatMessage(
                            role="user",
                            content=(
                                "请只用简短自然语言，基于上面的已校验结构化结果"
                                "回答最初请求；不要返回 JSON。"
                            ),
                        ),
                    ]
                    failure_stage = "chat"
                    assistant_text = await self._provider.complete(text_messages)
                    failure_stage = "internal"
            elif wants_text:
                messages = await self._build_chat_messages(run)
                failure_stage = "chat"
                assistant_text = await self._provider.complete(messages)
                failure_stage = "internal"

            if wants_image:
                failure_stage = "image"
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

            failure_stage = "internal"
            self._storage.complete_run_success(
                run["id"],
                assistant_text=assistant_text,
                structured_data=structured_data,
                output_file=output_file,
            )
        except StructuredOutputInvalid:
            LOGGER.warning(
                "structured_output_invalid request_id=%s run_id=%s",
                request_id,
                run["id"],
            )
            self._delete_generated(generated_rel_path)
            self._storage.mark_run_failed(
                run["id"],
                error_code=STRUCTURED_OUTPUT_INVALID,
                error_message="结构化输出不符合请求的 Schema。",
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
            if failure_stage == "chat":
                error_code = CHAT_PROVIDER_UNAVAILABLE
                error_message = "文本模型服务暂时不可用。"
            elif failure_stage == "image":
                error_code = IMAGE_PROVIDER_UNAVAILABLE
                error_message = "图片生成服务暂时不可用。"
            else:
                error_code = INTERNAL_ERROR
                error_message = "服务端内部错误。"
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
            content_parts: list[str] = []
            if row["content_text"]:
                content_parts.append(row["content_text"])
            if row["structured_data"]:
                structured_data = json.loads(row["structured_data"])
                content_parts.append(
                    json.dumps(
                        structured_data,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
            messages.append(
                ChatMessage(
                    role=row["role"],
                    content="\n".join(content_parts),
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
        # 启动恢复：在第一次轮询前执行
        if not self._recovery_done and self._destination_coordinator is not None:
            try:
                await asyncio.to_thread(self._recover_on_startup)
                self._recovery_done = True
            except Exception as exc:
                LOGGER.error(
                    "startup_recovery_failed error_type=%s",
                    type(exc).__name__,
                )

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

    def _recover_on_startup(self) -> None:
        """启动时执行恢复逻辑。

        扫描非终态 Destination 与 Scene，根据 Repository 里程碑决定继续哪个阶段。
        已提交对象不重做，清理未被引用的临时文件。
        """
        if self._destination_coordinator is None:
            return

        LOGGER.info("startup_recovery_started")

        try:
            # 恢复非终态 Destination
            dest_counts = self._destination_coordinator.recover_pending_destinations()
            LOGGER.info(
                "destination_recovery_completed recovered=%d skipped_done=%d",
                dest_counts["recovered_destinations"],
                dest_counts["skipped_done"],
            )
        except Exception as exc:
            LOGGER.error(
                "destination_recovery_failed error_type=%s",
                type(exc).__name__,
            )
            raise
