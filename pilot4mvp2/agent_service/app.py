"""会话 1 FastAPI 服务。"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import AuthenticatedClientId, hash_api_key
from .chat_provider import ChatModelProvider, OpenAICompatibleChatProvider
from .config import Settings, load_settings
from .errors import (
    IDEMPOTENCY_KEY_REUSED,
    RESOURCE_NOT_FOUND,
    VALIDATION_ERROR,
    ApiError,
    api_error_handler,
    unhandled_exception_handler,
    validation_error_payload,
)
from .ids import new_id
from .schemas import CreateRunRequest
from .storage import IdempotencyKeyReusedError, Storage
from .worker import RunWorker

LOGGER = logging.getLogger("uvicorn.error")


def _canonical_body_hash(body: CreateRunRequest) -> str:
    encoded = json.dumps(
        body.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_response(run: dict[str, Any], request_id: str) -> dict[str, Any]:
    response: dict[str, Any] = {
        "run_id": run["id"],
        "session_id": run["session_id"],
        "status": run["status"],
        "request_id": request_id,
    }
    if run["status"] == "succeeded":
        response["output"] = {"text": run["output_text"]}
    elif run["status"] == "failed":
        response["error"] = {
            "code": run["error_code"],
            "message": run["error_message"],
            "retryable": run["error_code"] in {
                "CHAT_PROVIDER_UNAVAILABLE",
                "SERVICE_RESTARTED",
            },
        }
    return response


def create_app(
    *,
    settings: Settings | None = None,
    provider: ChatModelProvider | None = None,
    start_worker: bool = True,
) -> FastAPI:
    """构建可注入 Provider 和临时数据库的会话 1 服务。"""
    resolved_settings = settings or load_settings()
    storage = Storage(resolved_settings.db_path)
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
    worker = RunWorker(
        storage=storage,
        provider=resolved_provider,
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
    app.state.worker = worker

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Any) -> Any:
        request.state.request_id = new_id("req")
        try:
            response = await call_next(request)
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
        if not body.response_format.is_text_only():
            raise ApiError(VALIDATION_ERROR, "会话 1 只支持文本输出。", status=400)
        if storage.get_session(body.session_id, api_client_id) is None:
            raise ApiError(RESOURCE_NOT_FOUND, "会话不存在。", status=404)

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
            )
        except IdempotencyKeyReusedError as exc:
            raise ApiError(
                IDEMPOTENCY_KEY_REUSED,
                "Idempotency-Key 已用于不同请求。",
                status=409,
            ) from exc
        return _run_response(run, request.state.request_id)

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(
        run_id: str,
        request: Request,
        api_client_id: AuthenticatedClientId,
    ) -> dict[str, Any]:
        run = storage.get_run(run_id, api_client_id)
        if run is None:
            raise ApiError(RESOURCE_NOT_FOUND, "Run 不存在。", status=404)
        return _run_response(run, request.state.request_id)

    return app
