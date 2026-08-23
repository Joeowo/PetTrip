"""Bearer API Key 鉴权。"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import AUTHENTICATION_FAILED, ApiError
from .storage import Storage


def hash_api_key(value: str) -> str:
    """返回稳定的 API Key 哈希，不持久化明文 Key。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_BEARER = HTTPBearer(auto_error=False, description="Pilot Bearer API Key")


def authenticated_client_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
) -> str:
    """验证 Bearer Key 并返回当前 API client 的不透明 ID。"""
    preauthenticated = getattr(request.state, "api_client_id", None)
    if isinstance(preauthenticated, str):
        return preauthenticated
    token = credentials.credentials if credentials is not None else None
    storage: Storage = request.app.state.storage
    client_id = storage.find_active_api_client_by_hash(hash_api_key(token)) if token else None
    if client_id is None:
        raise ApiError(
            AUTHENTICATION_FAILED,
            "认证失败。",
            status=401,
            retryable=False,
        )
    return client_id


AuthenticatedClientId = Annotated[str, Depends(authenticated_client_id)]
