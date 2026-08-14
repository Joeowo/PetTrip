"""会话 2 FastAPI Agent 服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Literal

from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import AuthenticatedClientId, hash_api_key
from .chat_provider import ChatModelProvider, OpenAICompatibleChatProvider
from .config import Settings, load_settings
from .image_provider import ImageGenerationProvider, OpenAICompatibleImageProvider
from .errors import (
    AUTHENTICATION_FAILED,
    FILE_TOO_LARGE,
    IDEMPOTENCY_KEY_REUSED,
    RESOURCE_NOT_FOUND,
    VALIDATION_ERROR,
    ApiError,
    api_error_handler,
    unhandled_exception_handler,
    validation_error_payload,
)
from .file_storage import LocalImageStorage
from .ids import new_id
from .schemas import CreateRunRequest
from .storage import (
    AttachmentTooLargeError,
    FileReferenceError,
    IdempotencyKeyReusedError,
    Storage,
)
from .worker import RunWorker

LOGGER = logging.getLogger("uvicorn.error")


def _canonical_body_hash(body: CreateRunRequest) -> str:
    encoded = json.dumps(
        body.model_dump(mode="json", exclude_defaults=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_response(file_row: dict[str, Any], request_id: str) -> dict[str, Any]:
    file_id = file_row["id"]
    return {
        "file_id": file_id,
        "source": file_row["source"],
        "purpose": file_row["purpose"],
        "mime_type": file_row["mime_type"],
        "size_bytes": file_row["size_bytes"],
        "sha256": file_row["sha256"],
        "width": file_row["width"],
        "height": file_row["height"],
        "created_at": file_row["created_at"],
        "download_url": f"/api/v1/files/{file_id}/content",
        "request_id": request_id,
    }


def _run_response(
    run: dict[str, Any], request_id: str, output_files: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "run_id": run["id"],
        "session_id": run["session_id"],
        "status": run["status"],
        "request_id": request_id,
    }
    if run["status"] == "succeeded":
        output: dict[str, Any] = {}
        if run["output_text"] is not None:
            output["text"] = run["output_text"]
        requested_modalities = json.loads(run["response_format"]).get("modalities", [])
        if (
            "structured_data" in requested_modalities
            and run["output_structured"] is not None
        ):
            output["structured_data"] = json.loads(run["output_structured"])
        if output_files:
            output["attachments"] = [
                {
                    "file_id": row["id"],
                    "source": row["source"],
                    "purpose": row["purpose"],
                    "mime_type": row["mime_type"],
                    "size_bytes": row["size_bytes"],
                    "sha256": row["sha256"],
                    "width": row["width"],
                    "height": row["height"],
                    "created_at": row["created_at"],
                    "download_url": f"/api/v1/files/{row['id']}/content",
                }
                for row in output_files
            ]
        response["output"] = output
    elif run["status"] == "failed":
        response["error"] = {
            "code": run["error_code"],
            "message": run["error_message"],
            "retryable": run["error_code"] in {
                "CHAT_PROVIDER_UNAVAILABLE",
                "IMAGE_PROVIDER_UNAVAILABLE",
                "SERVICE_RESTARTED",
            },
        }
    return response


def create_app(
    *,
    settings: Settings | None = None,
    provider: ChatModelProvider | None = None,
    image_provider: ImageGenerationProvider | None = None,
    start_worker: bool = True,
) -> FastAPI:
    """构建可注入 Provider 和临时数据库的会话 1 服务。"""
    resolved_settings = settings or load_settings()
    storage = Storage(resolved_settings.db_path)
    file_storage = LocalImageStorage(resolved_settings.data_dir)
    file_storage.remove_untracked_files(storage.list_file_paths())
    storage.upsert_api_client(
        hash_api_key(resolved_settings.pilot_api_key),
        "pilot-client",
    )
    resolved_provider = provider or OpenAICompatibleChatProvider(
        base_url=resolved_settings.chat_base_url,
        api_key=resolved_settings.chat_api_key,
        model=resolved_settings.chat_model,
        timeout_seconds=resolved_settings.chat_timeout,
        temperature=resolved_settings.chat_temperature,
        max_tokens=resolved_settings.chat_max_tokens,
    )
    resolved_image_provider = image_provider or OpenAICompatibleImageProvider(
        base_url=resolved_settings.image_base_url,
        api_key=resolved_settings.image_api_key,
        model=resolved_settings.image_model,
        timeout_seconds=resolved_settings.image_timeout,
        request_size=resolved_settings.image_request_size,
        max_decoded_bytes=resolved_settings.image_max_decoded_bytes,
        max_image_pixels=resolved_settings.image_max_pixels,
        generation_path=resolved_settings.image_generation_path,
    )
    worker = RunWorker(
        storage=storage,
        file_storage=file_storage,
        provider=resolved_provider,
        image_provider=resolved_image_provider,
        image_canvas_width=resolved_settings.image_canvas_width,
        image_canvas_height=resolved_settings.image_canvas_height,
        image_max_pixels=resolved_settings.image_max_pixels,
        poll_interval=resolved_settings.worker_poll_interval,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            worker.start()
        try:
            yield
        finally:
            await worker.stop()
            storage.close()

    app = FastAPI(
        title="PetTrip Agent Service",
        version=resolved_settings.service_version,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.storage = storage
    app.state.file_storage = file_storage
    app.state.worker = worker

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:
        request.state.request_id = new_id("req")
        try:
            if request.method == "POST" and request.url.path == "/api/v1/files":
                authorization = request.headers.get("authorization", "")
                scheme, _, token = authorization.partition(" ")
                client_id = None
                if scheme.lower() == "bearer" and token:
                    client_id = storage.find_active_api_client_by_hash(
                        hash_api_key(token)
                    )
                if client_id is None:
                    raise ApiError(
                        AUTHENTICATION_FAILED,
                        "认证失败。",
                        status=401,
                        retryable=False,
                    )
                request.state.api_client_id = client_id
                max_request_bytes = resolved_settings.max_upload_bytes + 64 * 1024
                content_length = request.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise ApiError(
                            VALIDATION_ERROR,
                            "Content-Length 不合法。",
                            status=400,
                        ) from exc
                    if declared_length > max_request_bytes:
                        raise ApiError(FILE_TOO_LARGE, "上传请求超过允许大小。")
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > max_request_bytes:
                        raise ApiError(FILE_TOO_LARGE, "上传请求超过允许大小。")
                request._body = bytes(body)
            response = await call_next(request)
        except ApiError as exc:
            response = await api_error_handler(request, exc)
        except Exception as exc:
            LOGGER.error(
                "request_failed request_id=%s method=%s path=%s error_type=%s",
                request.state.request_id,
                request.method,
                request.url.path,
                type(exc).__name__,
            )
            response = await unhandled_exception_handler(request, exc)
        response.headers["X-Request-ID"] = request.state.request_id
        LOGGER.info(
            "request_completed request_id=%s method=%s path=%s status=%s",
            request.state.request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response

    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if exc.status_code == 404:
            error = ApiError(RESOURCE_NOT_FOUND, "资源不存在。", status=404)
        elif exc.status_code == 405:
            error = ApiError(VALIDATION_ERROR, "请求方法不支持。", status=405)
        else:
            error = ApiError(VALIDATION_ERROR, "请求不合法。", status=exc.status_code)
        return await api_error_handler(request, error)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        error = ApiError(
            VALIDATION_ERROR,
            "请求参数不合法。",
            status=400,
            details=validation_error_payload(exc),
        )
        return await api_error_handler(request, error)

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        return {
            "status": "ok",
            "service_version": resolved_settings.service_version,
            "request_id": request.state.request_id,
        }

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(
        request: Request,
        api_client_id: AuthenticatedClientId,
    ) -> dict[str, Any]:
        session = storage.create_session(api_client_id)
        return {
            "session_id": session["id"],
            "created_at": session["created_at"],
            "request_id": request.state.request_id,
        }

    @app.get("/api/v1/sessions/{session_id}/messages")
    async def get_session_messages(
        session_id: str,
        request: Request,
        api_client_id: AuthenticatedClientId,
    ) -> dict[str, Any]:
        if storage.get_session(session_id, api_client_id) is None:
            raise ApiError(RESOURCE_NOT_FOUND, "会话不存在。", status=404)
        messages: list[dict[str, Any]] = []
        for row in storage.list_messages(session_id, api_client_id):
            attachments = storage.list_files_for_message(row["id"], api_client_id)
            messages.append(
                {
                    "message_id": row["id"],
                    "run_id": row["run_id"],
                    "role": row["role"],
                    "content_text": row["content_text"],
                    "structured_data": json.loads(row["structured_data"])
                    if row["structured_data"] is not None
                    else None,
                    "attachments": [
                        {
                            "file_id": file_row["id"],
                            "attachment_role": file_row["attachment_role"],
                            "source": file_row["source"],
                            "purpose": file_row["purpose"],
                            "mime_type": file_row["mime_type"],
                            "size_bytes": file_row["size_bytes"],
                            "sha256": file_row["sha256"],
                            "width": file_row["width"],
                            "height": file_row["height"],
                            "created_at": file_row["created_at"],
                            "download_url": (
                                f"/api/v1/files/{file_row['id']}/content"
                            ),
                        }
                        for file_row in attachments
                    ],
                    "created_at": row["created_at"],
                }
            )
        return {
            "session_id": session_id,
            "messages": messages,
            "request_id": request.state.request_id,
        }

    @app.post("/api/v1/files", status_code=201)
    async def upload_file(
        request: Request,
        api_client_id: AuthenticatedClientId,
        file: Annotated[UploadFile, File()],
        purpose: Annotated[Literal["vision_input", "reference_image"], Form()],
    ) -> dict[str, Any]:
        content = await file.read(resolved_settings.max_upload_bytes + 1)
        if len(content) > resolved_settings.max_upload_bytes:
            raise ApiError(FILE_TOO_LARGE, "图片文件超过允许大小。")
        file_id = new_id("file")
        stored = await asyncio.to_thread(
            file_storage.validate_and_store,
            file_id=file_id,
            filename=file.filename or "",
            declared_mime_type=file.content_type or "",
            data=content,
            max_bytes=resolved_settings.max_upload_bytes,
            max_dimension=resolved_settings.max_image_dimension,
            max_pixels=resolved_settings.max_image_pixels,
        )
        try:
            file_row = storage.create_file(
                file_id=file_id,
                api_client_id=api_client_id,
                source="user_upload",
                purpose=purpose,
                mime_type=stored.mime_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                width=stored.width,
                height=stored.height,
                rel_path=stored.rel_path,
            )
        except Exception:
            file_storage.delete(stored.rel_path)
            raise
        return _file_response(file_row, request.state.request_id)

    @app.get("/api/v1/files/{file_id}")
    async def get_file_metadata(
        file_id: str,
        request: Request,
        api_client_id: AuthenticatedClientId,
    ) -> dict[str, Any]:
        file_row = storage.get_file(file_id, api_client_id)
        if file_row is None:
            raise ApiError(RESOURCE_NOT_FOUND, "文件不存在。", status=404)
        return _file_response(file_row, request.state.request_id)

    @app.get("/api/v1/files/{file_id}/content")
    async def download_file(
        file_id: str,
        api_client_id: AuthenticatedClientId,
    ) -> FileResponse:
        file_row = storage.get_file(file_id, api_client_id)
        if file_row is None:
            raise ApiError(RESOURCE_NOT_FOUND, "文件不存在。", status=404)
        try:
            path = file_storage.resolve(file_row["rel_path"])
        except ValueError as exc:
            raise ApiError(RESOURCE_NOT_FOUND, "文件不存在。", status=404) from exc
        if not path.is_file():
            raise ApiError(RESOURCE_NOT_FOUND, "文件不存在。", status=404)
        return FileResponse(path, media_type=file_row["mime_type"])

    @app.post("/api/v1/runs", status_code=202)
    async def create_run(
        body: CreateRunRequest,
        request: Request,
        api_client_id: AuthenticatedClientId,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key or not idempotency_key.strip():
            raise ApiError(VALIDATION_ERROR, "缺少 Idempotency-Key。", status=400)
        if len(body.input.text) > resolved_settings.max_text_chars:
            raise ApiError(VALIDATION_ERROR, "输入文本过长。", status=400)
        modalities = body.response_format.modalities
        if len(set(modalities)) != len(modalities):
            raise ApiError(VALIDATION_ERROR, "输出模态不能重复。", status=400)
        if storage.get_session(body.session_id, api_client_id) is None:
            raise ApiError(RESOURCE_NOT_FOUND, "会话不存在。", status=404)
        attachment_ids = [item.file_id for item in body.input.attachments]
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ApiError(VALIDATION_ERROR, "附件不能重复。", status=400)
        key = idempotency_key.strip()
        body_hash = _canonical_body_hash(body)
        try:
            run = storage.create_run(
                api_client_id=api_client_id,
                session_id=body.session_id,
                request_input=body.input.model_dump(mode="json"),
                response_format=body.response_format.model_dump(mode="json"),
                idempotency_key=key,
                idempotency_body_hash=body_hash,
                max_attachment_bytes=resolved_settings.max_upload_bytes,
            )
        except IdempotencyKeyReusedError as exc:
            raise ApiError(
                IDEMPOTENCY_KEY_REUSED,
                "Idempotency-Key 已用于不同请求。",
                status=409,
            ) from exc
        except AttachmentTooLargeError as exc:
            raise ApiError(FILE_TOO_LARGE, "Run 附件总大小超过允许范围。") from exc
        except FileReferenceError as exc:
            raise ApiError(RESOURCE_NOT_FOUND, "附件不存在。", status=404) from exc
        output_files = (
            storage.list_output_files_for_run(run["id"], api_client_id)
            if run["status"] == "succeeded"
            else []
        )
        return _run_response(run, request.state.request_id, output_files)

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(
        run_id: str,
        request: Request,
        api_client_id: AuthenticatedClientId,
    ) -> dict[str, Any]:
        run = storage.get_run(run_id, api_client_id)
        if run is None:
            raise ApiError(RESOURCE_NOT_FOUND, "Run 不存在。", status=404)
        output_files = (
            storage.list_output_files_for_run(run_id, api_client_id)
            if run["status"] == "succeeded"
            else []
        )
        return _run_response(run, request.state.request_id, output_files)

    @app.get("/api/v1/runs/{run_id}/events")
    async def get_run_events(
        run_id: str,
        request: Request,
        api_client_id: AuthenticatedClientId,
    ) -> dict[str, Any]:
        run = storage.get_run(run_id, api_client_id)
        if run is None:
            raise ApiError(RESOURCE_NOT_FOUND, "Run 不存在。", status=404)
        events = storage.list_events(run_id, api_client_id)
        return {
            "run_id": run_id,
            "events": [
                {
                    "event_id": event["id"],
                    "event_type": event["event_type"],
                    "payload": json.loads(event["payload"])
                    if event["payload"]
                    else None,
                    "created_at": event["created_at"],
                }
                for event in events
            ],
            "request_id": request.state.request_id,
        }

    return app
