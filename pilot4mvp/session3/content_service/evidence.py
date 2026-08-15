"""会话3 JSON 证据脱敏与安全写盘。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "cookie",
    "set-cookie",
}
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
PROVIDER_RESPONSE_FIELDS = (
    "id",
    "object",
    "created_at",
    "completed_at",
    "status",
    "model",
    "error",
    "incomplete_details",
    "output",
    "output_text",
    "usage",
    "choices",
)


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return "<redacted-url>"
    if parts.scheme and parts.hostname:
        host = parts.hostname
        if ":" in host:
            host = f"[{host}]"
        netloc = host + (f":{port}" if port else "")
        return urlunsplit((parts.scheme.lower(), netloc, parts.path, "", ""))
    return value


def _redact_urls(value: str) -> str:
    return URL_PATTERN.sub(lambda match: _safe_url(match.group(0)), value)


def _secrets(value: str | tuple[str, ...]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else value


def redact(value: Any, api_key: str | tuple[str, ...] = "") -> Any:
    """递归移除敏感字段、URL 查询串及所有已知凭证的完整值。"""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                output[key] = "<redacted>"
            else:
                output[key] = redact(item, api_key)
        return output
    if isinstance(value, list):
        return [redact(item, api_key) for item in value]
    if isinstance(value, tuple):
        return [redact(item, api_key) for item in value]
    if isinstance(value, str):
        safe = value
        for secret in _secrets(api_key):
            if secret:
                safe = safe.replace(secret, "<redacted>")
        return _safe_url(safe) if safe.startswith(("http://", "https://")) else safe
    return value


def provider_response_evidence(value: Any, request_id: str | None = None) -> Any:
    """只保留验证 Provider 结果所需字段，排除上游内部指令和追踪标识。"""
    if not isinstance(value, dict):
        return {"body": value, "request_id": request_id}
    projected = {key: value[key] for key in PROVIDER_RESPONSE_FIELDS if key in value}
    projected["request_id"] = request_id
    return projected


def credential_hits(root: Path, secrets: tuple[str, ...]) -> list[str]:
    """返回包含完整凭证字节的相对文件名；从不返回或打印凭证内容。"""
    needles = [secret.encode() for secret in secrets if secret]
    if not needles:
        return []
    hits: list[str] = []
    for path in root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            if any(needle in content for needle in needles):
                hits.append(path.relative_to(root).as_posix())
    return hits


def write_json(path: Path, value: Any, api_key: str | tuple[str, ...] = "") -> None:
    """先脱敏再写 JSON，并断言所有完整 Key 均未进入文本证据。"""
    payload = json.dumps(redact(value, api_key), ensure_ascii=False, indent=2)
    if any(secret and secret in payload for secret in _secrets(api_key)):
        raise ValueError("refusing to persist evidence containing an API key")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8")
