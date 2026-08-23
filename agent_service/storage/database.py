"""SQLite 数据访问层 - 纯 CRUD 操作。"""

from __future__ import annotations

import hmac
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import is_expired, utcnow_iso
from ..shared.ids import new_id

# ---- Schema ---------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_clients (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    key_hash      TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    expires_at    TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    api_client_id TEXT NOT NULL REFERENCES api_clients(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                    TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES sessions(id),
    api_client_id         TEXT NOT NULL REFERENCES api_clients(id),
    status                TEXT NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued','running','succeeded','failed')),
    idempotency_key       TEXT NOT NULL,
    idempotency_body_hash TEXT NOT NULL,
    request_input         TEXT NOT NULL,
    response_format       TEXT NOT NULL,
    output_text           TEXT,
    output_structured     TEXT,
    error_code            TEXT,
    error_message         TEXT,
    created_at            TEXT NOT NULL,
    started_at            TEXT,
    completed_at          TEXT,
    UNIQUE (api_client_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    run_id          TEXT REFERENCES runs(id),
    role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content_text    TEXT,
    structured_data TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    event_type  TEXT NOT NULL,
    payload     TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id            TEXT PRIMARY KEY,
    api_client_id TEXT NOT NULL REFERENCES api_clients(id),
    source        TEXT NOT NULL CHECK (source IN ('user_upload','agent_generated')),
    purpose       TEXT NOT NULL CHECK (
        purpose IN ('vision_input','reference_image','generated_image','image_edit_result')
    ),
    mime_type     TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    width         INTEGER,
    height        INTEGER,
    rel_path      TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_files (
    message_id TEXT NOT NULL REFERENCES messages(id),
    file_id    TEXT NOT NULL REFERENCES files(id),
    role       TEXT NOT NULL CHECK (role IN ('input','output')),
    PRIMARY KEY (message_id, file_id)
);
"""


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    """单连接、锁串行化的 SQLite 数据访问对象。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # 自动提交；事务用显式 BEGIN/COMMIT
        )
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA foreign_keys = ON")

    def _migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """显式 ``BEGIN IMMEDIATE`` 事务，异常自动回滚。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # -- API Client --------------------------------------------------------

    def upsert_api_client(self, key_hash: str, name: str) -> str:
        """确保存在一个指定哈希的活跃客户端；已存在则返回其 id（幂等）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM api_clients WHERE key_hash = ?", (key_hash,)
            ).fetchone()
            if row is not None:
                return row["id"]
            client_id = new_id("client")
            self._conn.execute(
                "INSERT INTO api_clients(id, name, key_hash, status, created_at, expires_at) "
                "VALUES(?, ?, ?, 'active', ?, NULL)",
                (client_id, name, key_hash, utcnow_iso()),
            )
            return client_id

    def any_active_api_client(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM api_clients WHERE status = 'active' LIMIT 1"
            ).fetchone()
            return row is not None

    def find_active_api_client_by_hash(self, candidate_hash: str) -> str | None:
        """恒定时间比较未过期的活跃客户端哈希，返回匹配的客户端 id。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, key_hash, expires_at FROM api_clients WHERE status = 'active'"
            ).fetchall()
        for row in rows:
            matches = hmac.compare_digest(row["key_hash"], candidate_hash)
            if matches and not is_expired(row["expires_at"]):
                return row["id"]
        return None

    # -- Session -----------------------------------------------------------

    def create_session(self, api_client_id: str) -> dict[str, Any]:
        session_id = new_id("session")
        now = utcnow_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions(id, api_client_id, created_at, updated_at) "
                "VALUES(?, ?, ?, ?)",
                (session_id, api_client_id, now, now),
            )
        return {"id": session_id, "api_client_id": api_client_id, "created_at": now}

    def get_session(self, session_id: str, api_client_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ? AND api_client_id = ?",
                (session_id, api_client_id),
            ).fetchone()
        return _row_to_dict(row)

    def update_session_timestamp(self, session_id: str) -> None:
        """更新会话的 updated_at 时间戳。"""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (utcnow_iso(), session_id),
            )

    # -- Messages ----------------------------------------------------------

    def insert_message(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        run_id: str | None,
        role: str,
        content_text: str | None,
        structured_data: dict[str, Any] | None,
    ) -> str:
        """在事务内插入一条消息，返回 message_id。"""
        message_id = new_id("message")
        conn.execute(
            "INSERT INTO messages(id, session_id, run_id, role, content_text, "
            "structured_data, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                session_id,
                run_id,
                role,
                content_text,
                json.dumps(structured_data, ensure_ascii=False)
                if structured_data is not None
                else None,
                utcnow_iso(),
            ),
        )
        return message_id

    def list_messages(self, session_id: str, api_client_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.* FROM messages m "
                "JOIN sessions s ON s.id = m.session_id "
                "WHERE m.session_id = ? AND s.api_client_id = ? "
                "ORDER BY m.rowid ASC",
                (session_id, api_client_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_messages_for_run(
        self, session_id: str, api_client_id: str, run_id: str
    ) -> list[dict[str, Any]]:
        """返回成功历史和当前 Run 输入，排除其他未完成或失败 Run。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.* FROM messages m "
                "JOIN sessions s ON s.id = m.session_id "
                "JOIN runs r ON r.id = m.run_id "
                "WHERE m.session_id = ? AND s.api_client_id = ? "
                "AND (r.status = 'succeeded' OR r.id = ?) "
                "ORDER BY r.rowid ASC, "
                "CASE m.role WHEN 'user' THEN 0 ELSE 1 END, m.rowid ASC",
                (session_id, api_client_id, run_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_input_files_for_message(
        self,
        message_id: str,
        api_client_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.* FROM files f "
                "JOIN message_files mf ON mf.file_id = f.id "
                "WHERE mf.message_id = ? AND mf.role = 'input' "
                "AND f.api_client_id = ? ORDER BY mf.rowid ASC",
                (message_id, api_client_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_output_files_for_run(
        self, run_id: str, api_client_id: str
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.* FROM files f "
                "JOIN message_files mf ON mf.file_id = f.id "
                "JOIN messages m ON m.id = mf.message_id "
                "JOIN runs r ON r.id = m.run_id "
                "WHERE r.id = ? AND r.api_client_id = ? AND mf.role = 'output' "
                "ORDER BY mf.rowid ASC",
                (run_id, api_client_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_files_for_message(
        self, message_id: str, api_client_id: str
    ) -> list[dict[str, Any]]:
        """返回一条消息的安全文件元数据及输入/输出角色。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.*, mf.role AS attachment_role FROM files f "
                "JOIN message_files mf ON mf.file_id = f.id "
                "WHERE mf.message_id = ? AND f.api_client_id = ? "
                "ORDER BY mf.rowid ASC",
                (message_id, api_client_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def attach_file_to_message(
        self, conn: sqlite3.Connection, message_id: str, file_id: str, role: str
    ) -> None:
        """在事务内关联文件到消息。"""
        conn.execute(
            "INSERT INTO message_files(message_id, file_id, role) VALUES(?, ?, ?)",
            (message_id, file_id, role),
        )

    # -- Files --------------------------------------------------------------

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
        if Path(rel_path).is_absolute():
            raise ValueError("文件记录只能保存相对路径。")
        now = utcnow_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO files(id, api_client_id, source, purpose, mime_type, "
                "size_bytes, sha256, width, height, rel_path, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    file_id,
                    api_client_id,
                    source,
                    purpose,
                    mime_type,
                    size_bytes,
                    sha256,
                    width,
                    height,
                    rel_path,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return dict(row)

    def insert_file(
        self,
        conn: sqlite3.Connection,
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
    ) -> None:
        """在事务内插入文件记录（用于 Run 完成时的输出文件）。"""
        if Path(rel_path).is_absolute():
            raise ValueError("文件记录只能保存相对路径。")
        conn.execute(
            "INSERT INTO files(id, api_client_id, source, purpose, mime_type, "
            "size_bytes, sha256, width, height, rel_path, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                file_id,
                api_client_id,
                source,
                purpose,
                mime_type,
                size_bytes,
                sha256,
                width,
                height,
                rel_path,
                utcnow_iso(),
            ),
        )

    def get_file(self, file_id: str, api_client_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM files WHERE id = ? AND api_client_id = ?",
                (file_id, api_client_id),
            ).fetchone()
        return _row_to_dict(row)

    def get_file_for_attachment(
        self, conn: sqlite3.Connection, file_id: str, api_client_id: str
    ) -> dict[str, Any] | None:
        """在事务内查询文件（用于 Run 创建时验证附件）。"""
        row = conn.execute(
            "SELECT id, purpose, size_bytes FROM files "
            "WHERE id = ? AND api_client_id = ? AND source = 'user_upload'",
            (file_id, api_client_id),
        ).fetchone()
        return _row_to_dict(row)

    def list_file_paths(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT rel_path FROM files").fetchall()
        return {row["rel_path"] for row in rows}

    # -- Runs --------------------------------------------------------------

    def find_run_by_idempotency(
        self, api_client_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE api_client_id = ? AND idempotency_key = ?",
                (api_client_id, idempotency_key),
            ).fetchone()
        return _row_to_dict(row)

    def insert_run(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        session_id: str,
        api_client_id: str,
        idempotency_key: str,
        idempotency_body_hash: str,
        request_input: dict[str, Any],
        response_format: dict[str, Any],
    ) -> None:
        """在事务内插入一个新的 queued Run。"""
        conn.execute(
            "INSERT INTO runs(id, session_id, api_client_id, status, idempotency_key, "
            "idempotency_body_hash, request_input, response_format, created_at) "
            "VALUES(?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
            (
                run_id,
                session_id,
                api_client_id,
                idempotency_key,
                idempotency_body_hash,
                json.dumps(request_input, ensure_ascii=False),
                json.dumps(response_format, ensure_ascii=False),
                utcnow_iso(),
            ),
        )

    def get_run(self, run_id: str, api_client_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id = ? AND api_client_id = ?",
                (run_id, api_client_id),
            ).fetchone()
        return _row_to_dict(row)

    def get_run_in_transaction(
        self, conn: sqlite3.Connection, run_id: str
    ) -> dict[str, Any] | None:
        """在事务内查询 Run（用于状态转换）。"""
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row)

    def claim_next_queued_run(self) -> dict[str, Any] | None:
        """原子地把最早的 queued Run 置为 running 并返回；无则返回 None。"""
        now = utcnow_iso()
        with self.transaction() as conn:
            row = conn.execute(
                "UPDATE runs SET status = 'running', started_at = ? "
                "WHERE id = (SELECT id FROM runs WHERE status = 'queued' "
                "            ORDER BY rowid ASC LIMIT 1) "
                "RETURNING *",
                (now,),
            ).fetchone()
            if row is not None:
                self.insert_event(
                    conn,
                    run_id=row["id"],
                    event_type="run.started",
                    payload=None,
                )
        return _row_to_dict(row)

    def update_run_success(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        output_text: str | None,
        output_structured: dict[str, Any] | None,
    ) -> None:
        """在事务内更新 Run 状态为 succeeded。"""
        conn.execute(
            "UPDATE runs SET status = 'succeeded', output_text = ?, "
            "output_structured = ?, completed_at = ? WHERE id = ? AND status = 'running'",
            (
                output_text,
                json.dumps(output_structured, ensure_ascii=False)
                if output_structured is not None
                else None,
                utcnow_iso(),
                run_id,
            ),
        )

    def update_run_failed(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        error_code: str,
        error_message: str,
    ) -> int:
        """在事务内更新 Run 状态为 failed，返回影响行数。"""
        result = conn.execute(
            "UPDATE runs SET status = 'failed', error_code = ?, error_message = ?, "
            "completed_at = ? WHERE id = ? AND status = 'running'",
            (error_code, error_message, utcnow_iso(), run_id),
        )
        return result.rowcount

    def list_running_runs(self) -> list[dict[str, Any]]:
        """查询所有 running 状态的 Run（用于启动恢复）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM runs WHERE status = 'running'"
            ).fetchall()
        return [dict(row) for row in rows]

    def count_queued_runs(self) -> int:
        """统计 queued 状态的 Run 数量（用于启动恢复）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM runs WHERE status = 'queued'"
            ).fetchone()
        return row["cnt"] if row else 0

    # -- Events ------------------------------------------------------------

    def insert_event(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None,
    ) -> str:
        """在事务内插入事件。"""
        event_id = new_id("event")
        conn.execute(
            "INSERT INTO run_events(id, run_id, event_type, payload, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (
                event_id,
                run_id,
                event_type,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                utcnow_iso(),
            ),
        )
        return event_id

    def add_event(
        self, run_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        with self.transaction() as conn:
            self.insert_event(conn, run_id=run_id, event_type=event_type, payload=payload)

    def list_events(self, run_id: str, api_client_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.* FROM run_events e "
                "JOIN runs r ON r.id = e.run_id "
                "WHERE e.run_id = ? AND r.api_client_id = ? "
                "ORDER BY e.rowid ASC",
                (run_id, api_client_id),
            ).fetchall()
        return [dict(r) for r in rows]
