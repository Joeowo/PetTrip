"""SQLite 持久化层（spec §8）。

单进程 Pilot 使用一个共享 SQLite 连接，所有访问由进程内锁串行化；启用 WAL、
``busy_timeout`` 与外键。表结构覆盖 spec §8.1 的全部七张表；会话 2 使用
``files/message_files`` 保存图片元数据及消息附件关系。

图片二进制不入库；本层只保存相对路径与元数据。Base64、密钥、绝对路径均不落库。
"""

from __future__ import annotations

import hmac
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import SERVICE_RESTARTED
from .ids import new_id

# ---- 时间工具 -------------------------------------------------------------


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_expired(value: str | None) -> bool:
    if value is None:
        return False
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


class IdempotencyKeyReusedError(ValueError):
    """同一客户端把幂等键用于不同请求体。"""


class FileReferenceError(ValueError):
    """附件不存在、不属于当前客户端，或用途不一致。"""


class AttachmentTooLargeError(ValueError):
    """单个 Run 的附件总字节数超过允许上限。"""


# ---- 模式 -----------------------------------------------------------------

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


class Storage:
    """单连接、锁串行化的 SQLite 访问对象。"""

    def __init__(self, db_path: str | Path, *, recover: bool = True) -> None:
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
        if recover:
            self.recover_pending_runs()

    # -- 初始化 ------------------------------------------------------------
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
    def _transaction(self) -> Iterator[sqlite3.Connection]:
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
            if matches and not _is_expired(row["expires_at"]):
                return row["id"]
        return None

    # -- Session -----------------------------------------------------------
    def create_session(self, api_client_id: str) -> dict[str, Any]:
        session_id = new_id("session")
        now = utcnow_iso()
        with self._transaction() as conn:
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

    # -- Messages ----------------------------------------------------------
    def _insert_message(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        run_id: str | None,
        role: str,
        content_text: str | None,
        structured_data: dict[str, Any] | None,
    ) -> str:
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
        with self._transaction() as conn:
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

    def get_file(self, file_id: str, api_client_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM files WHERE id = ? AND api_client_id = ?",
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
        """原子查找或创建 queued Run，并写入用户消息与 ``run.queued`` 事件。"""
        run_id = new_id("run")
        now = utcnow_iso()
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM runs WHERE api_client_id = ? AND idempotency_key = ?",
                (api_client_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["idempotency_body_hash"] != idempotency_body_hash:
                    raise IdempotencyKeyReusedError(idempotency_key)
                return dict(existing)

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
                    now,
                ),
            )
            message_id = self._insert_message(
                conn,
                session_id=session_id,
                run_id=run_id,
                role="user",
                content_text=request_input.get("text") or "",
                structured_data=None,
            )
            attachment_bytes = 0
            for attachment in request_input.get("attachments") or []:
                file_row = conn.execute(
                    "SELECT id, purpose, size_bytes FROM files "
                    "WHERE id = ? AND api_client_id = ? AND source = 'user_upload'",
                    (attachment["file_id"], api_client_id),
                ).fetchone()
                if file_row is None or file_row["purpose"] != attachment["purpose"]:
                    raise FileReferenceError(attachment["file_id"])
                attachment_bytes += file_row["size_bytes"]
                if (
                    max_attachment_bytes is not None
                    and attachment_bytes > max_attachment_bytes
                ):
                    raise AttachmentTooLargeError(attachment_bytes)
                conn.execute(
                    "INSERT INTO message_files(message_id, file_id, role) "
                    "VALUES(?, ?, 'input')",
                    (message_id, file_row["id"]),
                )
            self._insert_event(
                conn,
                run_id=run_id,
                event_type="run.queued",
                payload={"idempotency_key": idempotency_key},
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row)

    def get_run(self, run_id: str, api_client_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id = ? AND api_client_id = ?",
                (run_id, api_client_id),
            ).fetchone()
        return _row_to_dict(row)

    def claim_next_queued_run(self) -> dict[str, Any] | None:
        """原子地把最早的 queued Run 置为 running 并返回；无则返回 None。"""
        now = utcnow_iso()
        with self._transaction() as conn:
            row = conn.execute(
                "UPDATE runs SET status = 'running', started_at = ? "
                "WHERE id = (SELECT id FROM runs WHERE status = 'queued' "
                "            ORDER BY rowid ASC LIMIT 1) "
                "RETURNING *",
                (now,),
            ).fetchone()
            if row is not None:
                self._insert_event(
                    conn,
                    run_id=row["id"],
                    event_type="run.started",
                    payload=None,
                )
        return _row_to_dict(row)

    def complete_run_success(
        self,
        run_id: str,
        *,
        assistant_text: str | None,
        structured_data: dict[str, Any] | None = None,
        output_file: dict[str, Any] | None = None,
    ) -> None:
        """在单事务内写入助手消息、输出文件关系与完成事件。"""
        now = utcnow_iso()
        with self._transaction() as conn:
            run = conn.execute(
                "SELECT session_id, api_client_id FROM runs "
                "WHERE id = ? AND status = 'running'",
                (run_id,),
            ).fetchone()
            if run is None:
                raise RuntimeError("Run 必须处于 running 状态才能提交成功结果。")
            if output_file is not None:
                conn.execute(
                    "INSERT INTO files(id, api_client_id, source, purpose, mime_type, "
                    "size_bytes, sha256, width, height, rel_path, created_at) "
                    "VALUES(?, ?, 'agent_generated', 'generated_image', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        output_file["id"],
                        run["api_client_id"],
                        output_file["mime_type"],
                        output_file["size_bytes"],
                        output_file["sha256"],
                        output_file["width"],
                        output_file["height"],
                        output_file["rel_path"],
                        now,
                    ),
                )
            message_id = self._insert_message(
                conn,
                session_id=run["session_id"],
                run_id=run_id,
                role="assistant",
                content_text=assistant_text,
                structured_data=structured_data,
            )
            if output_file is not None:
                conn.execute(
                    "INSERT INTO message_files(message_id, file_id, role) "
                    "VALUES(?, ?, 'output')",
                    (message_id, output_file["id"]),
                )
                self._insert_event(
                    conn,
                    run_id=run_id,
                    event_type="artifact.created",
                    payload={
                        "file_id": output_file["id"],
                        "message_id": message_id,
                    },
                )
            self._insert_event(
                conn,
                run_id=run_id,
                event_type="message.created",
                payload={"role": "assistant", "message_id": message_id},
            )
            conn.execute(
                "UPDATE runs SET status = 'succeeded', output_text = ?, "
                "output_structured = ?, completed_at = ? WHERE id = ? AND status = 'running'",
                (
                    assistant_text,
                    json.dumps(structured_data, ensure_ascii=False)
                    if structured_data is not None
                    else None,
                    now,
                    run_id,
                ),
            )
            self._insert_event(
                conn, run_id=run_id, event_type="run.completed", payload=None
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, run["session_id"])
            )

    def mark_run_failed(
        self, run_id: str, *, error_code: str, error_message: str
    ) -> None:
        now = utcnow_iso()
        with self._transaction() as conn:
            result = conn.execute(
                "UPDATE runs SET status = 'failed', error_code = ?, error_message = ?, "
                "completed_at = ? WHERE id = ? AND status = 'running'",
                (error_code, error_message, now, run_id),
            )
            if result.rowcount:
                self._insert_event(
                    conn,
                    run_id=run_id,
                    event_type="run.failed",
                    payload={"code": error_code},
                )

    # -- Events ------------------------------------------------------------
    def _insert_event(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None,
    ) -> str:
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
        with self._transaction() as conn:
            self._insert_event(conn, run_id=run_id, event_type=event_type, payload=payload)

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

    # -- 启动恢复（spec §6.4）----------------------------------------------
    def recover_pending_runs(self) -> dict[str, int]:
        """遗留 ``running`` Run 标记为 ``failed(SERVICE_RESTARTED)``；``queued`` 保留。"""
        counts = {"recovered_running": 0, "kept_queued": 0}
        with self._lock:
            running = self._conn.execute(
                "SELECT id FROM runs WHERE status = 'running'"
            ).fetchall()
            queued = self._conn.execute(
                "SELECT 1 FROM runs WHERE status = 'queued'"
            ).fetchall()
        counts["kept_queued"] = len(queued)
        for row in running:
            self.mark_run_failed(
                row["id"],
                error_code=SERVICE_RESTARTED,
                error_message="服务重启，遗留的 running Run 已终止，请重新创建。",
            )
            counts["recovered_running"] += 1
        return counts
