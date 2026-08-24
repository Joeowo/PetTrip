from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_service.shared.ids import new_id
from agent_service.storage import Storage
from agent_service.storage.destination_storage import DestinationRepository


def _open_repo(tmp_path: Path) -> tuple[Storage, DestinationRepository, str, dict]:
    storage = Storage(tmp_path / "snapshot.db", recover=False)
    client_id = storage.upsert_api_client("snapshot-key", "snapshot-client")
    session = storage.create_session(client_id)
    run = storage.create_run(
        api_client_id=client_id,
        session_id=session["id"],
        request_input={"type": "agent.generate"},
        response_format={"modalities": ["text"]},
        idempotency_key="snapshot-idempotency",
        idempotency_body_hash="c" * 64,
    )
    repo = DestinationRepository(tmp_path / "snapshot.db")
    repo.open()
    destination = repo.create_destination(
        session_id=session["id"], api_client_id=client_id
    )
    return storage, repo, client_id, {"run": run, "destination": destination}


def test_snapshot_binding_schema_is_idempotent_and_run_is_bound_once(tmp_path: Path) -> None:
    storage, repo, client_id, records = _open_repo(tmp_path)
    try:
        snapshot_id = new_id("snapshot")
        spec_id = new_id("spec")
        requirements_id = new_id("requirements")
        now = "2026-08-24T00:00:00Z"
        with repo.transaction() as conn:
            conn.execute(
                "INSERT INTO destination_requirements "
                "(requirements_id, destination_id, source_inputs, frozen_at, sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (requirements_id, records["destination"]["id"], "[]", now, "a" * 64, now),
            )
            conn.execute(
                "INSERT INTO destination_specs "
                "(spec_id, destination_id, spec_version, template_id, template_version, "
                "requirements_id, requirements_sha256, title, shared_environment_spec, "
                "locked_at, sha256, created_at) VALUES (?, ?, 1, 't', '1', ?, ?, 'title', '{}', ?, ?, ?)",
                (spec_id, records["destination"]["id"], requirements_id, "a" * 64, now, "b" * 64, now),
            )
            conn.execute(
                "INSERT INTO destination_snapshots "
                "(snapshot_id, destination_id, api_client_id, requirements_id, requirements_sha256, "
                "spec_id, spec_version, spec_sha256, schema_name, schema_version, frozen_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'agent_panel_snapshot', '0.1', ?, ?)",
                (snapshot_id, records["destination"]["id"], client_id, requirements_id, "a" * 64, spec_id, "b" * 64, now, now),
            )
            conn.execute(
                "INSERT INTO run_snapshot_bindings (run_id, snapshot_id, bound_at) VALUES (?, ?, ?)",
                (records["run"]["id"], snapshot_id, now),
            )

        bound = repo.get_snapshot_for_run(records["run"]["id"])
        assert bound is not None
        assert bound["snapshot_id"] == snapshot_id
        assert bound["api_client_id"] == client_id

        with pytest.raises(sqlite3.IntegrityError):
            with repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO run_snapshot_bindings (run_id, snapshot_id, bound_at) VALUES (?, ?, ?)",
                    (records["run"]["id"], new_id("snapshot"), now),
                )
        with pytest.raises(sqlite3.IntegrityError):
            with repo.transaction() as conn:
                conn.execute(
                    "UPDATE destination_snapshots SET spec_version = 2 WHERE snapshot_id = ?",
                    (snapshot_id,),
                )

        repo.close()
        repo.open()
        assert repo.get_snapshot_for_run(records["run"]["id"])["snapshot_id"] == snapshot_id
    finally:
        repo.close()
        storage.close()


def test_snapshot_tables_survive_existing_database_restart(tmp_path: Path) -> None:
    storage, repo, _, _ = _open_repo(tmp_path)
    repo.close()
    storage.close()

    repo2 = DestinationRepository(tmp_path / "snapshot.db")
    repo2.open()
    try:
        tables = {
            row["name"]
            for row in repo2._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"destination_snapshots", "run_snapshot_bindings", "snapshot_shared_environment_bindings", "snapshot_scene_artifact_bindings"} <= tables
    finally:
        repo2.close()
