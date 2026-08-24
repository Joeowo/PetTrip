"""Storage 层 - 数据持久化与向后兼容层。

向后兼容导出：新代码应该直接使用 storage.database.Database 和 domain.runs。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.runs import (
    complete_run_success,
    create_run,
    create_clarification_run,
    mark_run_failed,
    recover_pending_runs,
)
from .database import Database
from .destination_storage import DestinationRepository
from .models import (
    AttachmentTooLargeError,
    FileReferenceError,
    IdempotencyKeyReusedError,
    ClarificationAlreadyClosedError,
    InputIdConflictError,
    utcnow_iso,
)

__all__ = [
    "Storage",
    "IdempotencyKeyReusedError",
    "FileReferenceError",
    "AttachmentTooLargeError",
    "ClarificationAlreadyClosedError",
    "InputIdConflictError",
    "utcnow_iso",
    "Database",
    "DestinationRepository",
]


class Storage:
    """向后兼容的 Storage 类，委托给新的分层架构。"""

    def __init__(self, db_path: str | Path, *, recover: bool = True) -> None:
        self._db = Database(db_path)
        if recover:
            self.recover_pending_runs()

    @property
    def _conn(self):
        """向后兼容：暴露内部连接对象供测试使用。"""
        return self._db._conn

    def close(self) -> None:
        self._db.close()

    # -- 直接委托给 Database 的方法 ---------------------------------------

    def upsert_api_client(self, key_hash: str, name: str) -> str:
        return self._db.upsert_api_client(key_hash, name)

    def any_active_api_client(self) -> bool:
        return self._db.any_active_api_client()

    def find_active_api_client_by_hash(self, candidate_hash: str) -> str | None:
        return self._db.find_active_api_client_by_hash(candidate_hash)

    def create_session(self, api_client_id: str) -> dict[str, Any]:
        return self._db.create_session(api_client_id)

    def get_session(self, session_id: str, api_client_id: str) -> dict[str, Any] | None:
        return self._db.get_session(session_id, api_client_id)

    def list_messages(self, session_id: str, api_client_id: str) -> list[dict[str, Any]]:
        return self._db.list_messages(session_id, api_client_id)

    def list_messages_for_run(
        self, session_id: str, api_client_id: str, run_id: str
    ) -> list[dict[str, Any]]:
        return self._db.list_messages_for_run(session_id, api_client_id, run_id)

    def list_input_files_for_message(
        self, message_id: str, api_client_id: str
    ) -> list[dict[str, Any]]:
        return self._db.list_input_files_for_message(message_id, api_client_id)

    def list_output_files_for_run(
        self, run_id: str, api_client_id: str
    ) -> list[dict[str, Any]]:
        return self._db.list_output_files_for_run(run_id, api_client_id)

    def list_files_for_message(
        self, message_id: str, api_client_id: str
    ) -> list[dict[str, Any]]:
        return self._db.list_files_for_message(message_id, api_client_id)

    def create_file(
        self,
        *,
        file_id: str,
        api_client_id: str,
        source: str,
        purpose: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        width: int,
        height: int,
        rel_path: str,
    ) -> dict[str, Any]:
        return self._db.create_file(
            file_id=file_id,
            api_client_id=api_client_id,
            source=source,
            purpose=purpose,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            width=width,
            height=height,
            rel_path=rel_path,
        )

    def delete_file(self, file_id: str) -> None:
        self._db.delete_file(file_id)

    def get_file(self, file_id: str, api_client_id: str) -> dict[str, Any] | None:
        return self._db.get_file(file_id, api_client_id)

    def list_file_paths(self) -> set[str]:
        return self._db.list_file_paths()

    def find_run_by_idempotency(
        self, api_client_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        return self._db.find_run_by_idempotency(api_client_id, idempotency_key)

    def get_run(self, run_id: str, api_client_id: str) -> dict[str, Any] | None:
        return self._db.get_run(run_id, api_client_id)

    def claim_next_queued_run(self) -> dict[str, Any] | None:
        return self._db.claim_next_queued_run()

    def add_event(
        self, run_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        return self._db.add_event(run_id, event_type, payload)

    def list_events(self, run_id: str, api_client_id: str) -> list[dict[str, Any]]:
        return self._db.list_events(run_id, api_client_id)

    # -- 委托给 domain.runs 的方法 -----------------------------------------

    def create_run(
        self,
        *,
        api_client_id: str,
        session_id: str,
        request_input: dict[str, Any],
        response_format: dict[str, Any],
        idempotency_key: str,
        idempotency_body_hash: str,
        max_attachment_bytes: int | None = None,
    ) -> dict[str, Any]:
        return create_run(
            self._db,
            api_client_id=api_client_id,
            session_id=session_id,
            request_input=request_input,
            response_format=response_format,
            idempotency_key=idempotency_key,
            idempotency_body_hash=idempotency_body_hash,
            max_attachment_bytes=max_attachment_bytes,
        )

    def create_clarification_run(
        self,
        *,
        api_client_id: str,
        session_id: str,
        command: dict[str, Any],
        idempotency_key: str,
        idempotency_body_hash: str,
        classified_result: dict[str, Any] | None = None,
        assistant_reply: str | None = None,
    ) -> dict[str, Any]:
        """创建澄清命令的 Run（T2: submit_input 或 close）。"""
        return create_clarification_run(
            self._db,
            api_client_id=api_client_id,
            session_id=session_id,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_body_hash=idempotency_body_hash,
            classified_result=classified_result,
            assistant_reply=assistant_reply,
        )

    def complete_run_success(
        self,
        run_id: str,
        *,
        assistant_text: str | None,
        structured_data: dict[str, Any] | None = None,
        output_file: dict[str, Any] | None = None,
    ) -> None:
        return complete_run_success(
            self._db,
            run_id,
            assistant_text=assistant_text,
            structured_data=structured_data,
            output_file=output_file,
        )

    def mark_run_failed(
        self, run_id: str, *, error_code: str, error_message: str
    ) -> None:
        return mark_run_failed(
            self._db, run_id, error_code=error_code, error_message=error_message
        )

    def recover_pending_runs(self) -> dict[str, int]:
        return recover_pending_runs(self._db)
