"""数据模型、异常定义和时间工具。"""

from datetime import datetime, timezone


# ---- 异常定义 -------------------------------------------------------------


class IdempotencyKeyReusedError(ValueError):
    """同一客户端把幂等键用于不同请求体。"""


class FileReferenceError(ValueError):
    """附件不存在、不属于当前客户端，或用途不一致。"""


class AttachmentTooLargeError(ValueError):
    """单个 Run 的附件总字节数超过允许上限。"""


# ---- 时间工具 -------------------------------------------------------------


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_expired(value: str | None) -> bool:
    if value is None:
        return False
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)
