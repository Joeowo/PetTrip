"""稳定错误契约（spec §10）。

服务端对外只返回稳定错误码与通用文案，不把 Provider 堆栈、原始响应、密钥或文件路径
透传给客户端。所有错误响应与日志都含 ``request_id``；Run 级错误同时含 ``run_id``。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

# ---- 错误码（spec §10 表）-------------------------------------------------
AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
FILE_TYPE_UNSUPPORTED = "FILE_TYPE_UNSUPPORTED"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
FILE_DECODE_FAILED = "FILE_DECODE_FAILED"
CHAT_PROVIDER_UNAVAILABLE = "CHAT_PROVIDER_UNAVAILABLE"
IMAGE_PROVIDER_UNAVAILABLE = "IMAGE_PROVIDER_UNAVAILABLE"
STRUCTURED_OUTPUT_INVALID = "STRUCTURED_OUTPUT_INVALID"
IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
CLARIFICATION_ALREADY_CLOSED = "CLARIFICATION_ALREADY_CLOSED"
SERVICE_RESTARTED = "SERVICE_RESTARTED"
INTERNAL_ERROR = "INTERNAL_ERROR"
SNAPSHOT_NOT_READY = "SNAPSHOT_NOT_READY"
SNAPSHOT_INCONSISTENT = "SNAPSHOT_INCONSISTENT"
UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"

_DEFAULT_STATUS: dict[str, int] = {
    AUTHENTICATION_FAILED: 401,
    RESOURCE_NOT_FOUND: 404,
    VALIDATION_ERROR: 400,
    FILE_TYPE_UNSUPPORTED: 400,
    FILE_TOO_LARGE: 400,
    FILE_DECODE_FAILED: 400,
    CHAT_PROVIDER_UNAVAILABLE: 503,
    IMAGE_PROVIDER_UNAVAILABLE: 503,
    STRUCTURED_OUTPUT_INVALID: 422,
    IDEMPOTENCY_KEY_REUSED: 409,
    CLARIFICATION_ALREADY_CLOSED: 409,
    SERVICE_RESTARTED: 409,
    INTERNAL_ERROR: 500,
    SNAPSHOT_NOT_READY: 409,
    SNAPSHOT_INCONSISTENT: 409,
    UNSUPPORTED_SCHEMA: 422,
}

_RETRYABLE_DEFAULT: dict[str, bool] = {
    AUTHENTICATION_FAILED: False,
    RESOURCE_NOT_FOUND: False,
    VALIDATION_ERROR: False,
    FILE_TYPE_UNSUPPORTED: False,
    FILE_TOO_LARGE: False,
    FILE_DECODE_FAILED: False,
    CHAT_PROVIDER_UNAVAILABLE: True,
    IMAGE_PROVIDER_UNAVAILABLE: True,
    STRUCTURED_OUTPUT_INVALID: False,
    IDEMPOTENCY_KEY_REUSED: False,
    CLARIFICATION_ALREADY_CLOSED: False,
    SERVICE_RESTARTED: True,  # “可重新创建 Run”
    INTERNAL_ERROR: False,
    SNAPSHOT_NOT_READY: True,
    SNAPSHOT_INCONSISTENT: False,
    UNSUPPORTED_SCHEMA: False,
}


class ApiError(Exception):
    """携带稳定错误码的服务端异常，由统一处理器转成对客户端的错误响应。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status if status is not None else _DEFAULT_STATUS.get(code, 500)
        self.retryable = (
            retryable if retryable is not None else _RETRYABLE_DEFAULT.get(code, False)
        )
        self.details = details
        super().__init__(message)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """把 ``ApiError`` 转成统一错误对象。``request_id`` 由中间件统一注入。"""
    error: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
    }
    if exc.details:
        error["details"] = exc.details
    return JSONResponse(
        status_code=exc.status,
        content={"error": error, "request_id": _request_id(request)},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：未分类异常一律映射为 ``INTERNAL_ERROR``，不向客户端泄露细节。"""
    # 服务端记录完整异常（日志脱敏由调用方保证不写入密钥），客户端只见通用文案。
    request_id = _request_id(request)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": INTERNAL_ERROR,
                "message": "服务端内部错误。",
                "retryable": False,
            },
            "request_id": request_id,
        },
    )


def validation_error_payload(exc: Any) -> list[dict[str, Any]]:
    """从 FastAPI 的 RequestValidationError 提取可读字段错误（不含敏感值）。"""
    details: list[dict[str, Any]] = []
    for err in getattr(exc, "errors", lambda: [])():
        loc = ".".join(str(part) for part in err.get("loc", []))
        details.append({"loc": loc, "msg": err.get("msg", "")})
    return details
