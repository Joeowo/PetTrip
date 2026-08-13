from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pilot4mvp2.agent_service.auth import hash_api_key
from pilot4mvp2.agent_service.storage import FileReferenceError, Storage


def _create_queued_run(storage: Storage) -> tuple[str, str]:
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)
    run = storage.create_run(
        api_client_id=client_id,
        session_id=session["id"],
        request_input={"text": "你好"},
        response_format={"modalities": ["text"]},
        idempotency_key="idem-1",
        idempotency_body_hash="hash-1",
    )
    return client_id, run["id"]


def test_storage_initializes_required_schema_and_pragmas(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    try:
        pragmas = {
            "journal_mode": storage._conn.execute("PRAGMA journal_mode").fetchone()[0],
            "busy_timeout": storage._conn.execute("PRAGMA busy_timeout").fetchone()[0],
            "foreign_keys": storage._conn.execute("PRAGMA foreign_keys").fetchone()[0],
        }
        tables = {
            row[0]
            for row in storage._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        storage.close()

    assert pragmas == {"journal_mode": "wal", "busy_timeout": 5000, "foreign_keys": 1}
    assert {
        "api_clients",
        "sessions",
        "messages",
        "runs",
        "run_events",
        "files",
        "message_files",
    }.issubset(tables)


def test_run_lifecycle_records_messages_and_events(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    try:
        client_id, run_id = _create_queued_run(storage)
        claimed = storage.claim_next_queued_run()
        assert claimed is not None and claimed["id"] == run_id

        storage.complete_run_success(run_id, assistant_text="你好，我可以回复文本。")
        run = storage.get_run(run_id, client_id)
        messages = storage.list_messages(claimed["session_id"], client_id)
        events = storage.list_events(run_id, client_id)

        assert run is not None and run["status"] == "succeeded"
        assert [row["role"] for row in messages] == ["user", "assistant"]
        assert [row["event_type"] for row in events] == [
            "run.queued",
            "run.started",
            "message.created",
            "run.completed",
        ]

        storage.mark_run_failed(
            run_id,
            error_code="CHAT_PROVIDER_UNAVAILABLE",
            error_message="不应覆盖终态。",
        )
        assert storage.get_run(run_id, client_id)["status"] == "succeeded"  # type: ignore[index]
    finally:
        storage.close()


def test_recovery_fails_running_run_and_keeps_queued(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    storage = Storage(db_path, recover=False)
    client_id, running_id = _create_queued_run(storage)
    storage.claim_next_queued_run()
    session = storage.create_session(client_id)
    queued = storage.create_run(
        api_client_id=client_id,
        session_id=session["id"],
        request_input={"text": "继续"},
        response_format={"modalities": ["text"]},
        idempotency_key="idem-2",
        idempotency_body_hash="hash-2",
    )
    storage.close()

    recovered = Storage(db_path)
    try:
        running = recovered.get_run(running_id, client_id)
        still_queued = recovered.get_run(queued["id"], client_id)
        assert running is not None
        assert running["status"] == "failed"
        assert running["error_code"] == "SERVICE_RESTARTED"
        assert still_queued is not None and still_queued["status"] == "queued"
    finally:
        recovered.close()


def test_expired_api_key_is_rejected(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    try:
        key_hash = hash_api_key("expired-key")
        client_id = storage.upsert_api_client(key_hash, "expired-client")
        storage._conn.execute(
            "UPDATE api_clients SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00Z", client_id),
        )
        assert storage.find_active_api_client_by_hash(key_hash) is None

        storage._conn.execute(
            "UPDATE api_clients SET expires_at = ? WHERE id = ?",
            ("2999-01-01T00:00:00Z", client_id),
        )
        assert storage.find_active_api_client_by_hash(key_hash) == client_id
    finally:
        storage.close()


def test_invalid_file_reference_rolls_back_run_transaction(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    try:
        client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
        session = storage.create_session(client_id)
        with pytest.raises(FileReferenceError):
            storage.create_run(
                api_client_id=client_id,
                session_id=session["id"],
                request_input={
                    "text": "无效附件",
                    "attachments": [
                        {"file_id": "file_missing", "purpose": "vision_input"}
                    ],
                },
                response_format={"modalities": ["text"]},
                idempotency_key="bad-file",
                idempotency_body_hash="bad-file-hash",
            )

        for table in ("runs", "messages", "message_files", "run_events"):
            count = storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0
    finally:
        storage.close()


def test_idempotent_run_creation_is_atomic(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    try:
        client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
        session = storage.create_session(client_id)

        def create() -> str:
            run = storage.create_run(
                api_client_id=client_id,
                session_id=session["id"],
                request_input={"text": "同一请求"},
                response_format={"modalities": ["text"]},
                idempotency_key="concurrent-idem",
                idempotency_body_hash="same-hash",
            )
            return run["id"]

        with ThreadPoolExecutor(max_workers=4) as executor:
            run_ids = list(executor.map(lambda _: create(), range(8)))

        assert len(set(run_ids)) == 1
        messages = storage.list_messages(session["id"], client_id)
        assert [(row["role"], row["content_text"]) for row in messages] == [
            ("user", "同一请求")
        ]
    finally:
        storage.close()
