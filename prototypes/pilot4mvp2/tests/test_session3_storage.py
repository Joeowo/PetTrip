from __future__ import annotations

from pathlib import Path

import pytest

from pilot4mvp2.agent_service.auth import hash_api_key
from pilot4mvp2.agent_service.storage import Storage


def _running_run(storage: Storage) -> tuple[str, str]:
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)
    run = storage.create_run(
        api_client_id=client_id,
        session_id=session["id"],
        request_input={"text": "生成图片"},
        response_format={"modalities": ["image"]},
        idempotency_key="image-output",
        idempotency_body_hash="image-output-hash",
    )
    storage.claim_next_queued_run()
    return client_id, run["id"]


def _output_file(file_id: str) -> dict[str, object]:
    return {
        "id": file_id,
        "mime_type": "image/png",
        "size_bytes": 8,
        "sha256": "a" * 64,
        "width": 64,
        "height": 48,
        "rel_path": "files/generated/" + file_id + ".png",
    }


def test_image_success_commits_file_relation_and_artifact_event(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    try:
        client_id, run_id = _running_run(storage)
        storage.complete_run_success(
            run_id,
            assistant_text=None,
            output_file=_output_file("file_output"),
        )
        run = storage.get_run(run_id, client_id)
        files = storage.list_output_files_for_run(run_id, client_id)
        events = storage.list_events(run_id, client_id)
        relation = storage._conn.execute(
            "SELECT role FROM message_files WHERE file_id = ?", ("file_output",)
        ).fetchone()

        assert run is not None and run["status"] == "succeeded"
        assert run["output_text"] is None
        assert files[0]["id"] == "file_output"
        assert relation["role"] == "output"
        assert [event["event_type"] for event in events] == [
            "run.queued",
            "run.started",
            "artifact.created",
            "message.created",
            "run.completed",
        ]
    finally:
        storage.close()


def test_image_success_rolls_back_all_records_on_file_conflict(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    try:
        client_id, run_id = _running_run(storage)
        existing_client = storage.upsert_api_client(hash_api_key("other"), "other")
        storage.create_file(
            file_id="file_conflict",
            api_client_id=existing_client,
            source="user_upload",
            purpose="vision_input",
            mime_type="image/png",
            size_bytes=1,
            sha256="b" * 64,
            width=1,
            height=1,
            rel_path="files/input/file_conflict.png",
        )

        with pytest.raises(Exception):
            storage.complete_run_success(
                run_id,
                assistant_text=None,
                output_file=_output_file("file_conflict"),
            )

        run = storage.get_run(run_id, client_id)
        assert run is not None and run["status"] == "running"
        assert storage._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE run_id = ? AND role = 'assistant'",
            (run_id,),
        ).fetchone()[0] == 0
        assert storage._conn.execute(
            "SELECT COUNT(*) FROM message_files WHERE role = 'output'"
        ).fetchone()[0] == 0
    finally:
        storage.close()
