from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from agent_service.api.app import create_app
from agent_service.shared.config import Settings


AUTH = {"Authorization": "Bearer issue58-key"}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        service_version="0.58.0-test",
        host="127.0.0.1",
        port=8001,
        pilot_root=tmp_path,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "issue58.db",
        chat_base_url="https://fixture.invalid/v1",
        chat_api_key="fixture-chat",
        chat_model="fixture-chat-model",
        chat_timeout=1,
        chat_temperature=0,
        chat_max_tokens=256,
        pilot_api_key="issue58-key",
        worker_poll_interval=0.001,
        max_text_chars=200,
        image_base_url="https://fixture.invalid/v1",
        image_api_key="fixture-image",
        image_model="fixture-image-model",
        image_timeout=1,
        image_request_size="1024x1024",
        image_canvas_width=64,
        image_canvas_height=48,
        image_max_decoded_bytes=2_000_000,
    )


def test_panel_snapshot_requires_authentication(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), start_worker=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/runs/run_missing/panel-snapshot")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"


def test_panel_snapshot_unknown_run_is_not_found(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), start_worker=False)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/runs/run_missing/panel-snapshot", headers=AUTH
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_panel_snapshot_openapi_declares_bearer_and_response_model(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), start_worker=False)
    with TestClient(app) as client:
        operation = client.get("/openapi.json").json()["paths"][
            "/api/v1/runs/{run_id}/panel-snapshot"
        ]["get"]

    assert "security" in operation
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PanelSnapshotResponse"
    )


def test_panel_snapshot_returns_immutable_identity_and_supports_etag(
    tmp_path: Path,
) -> None:
    app = create_app(settings=_settings(tmp_path), start_worker=False)
    with TestClient(app) as client:
        session_response = client.post("/api/v1/sessions", headers=AUTH)
        session_id = session_response.json()["session_id"]
        run_response = client.post(
            "/api/v1/runs",
            headers={**AUTH, "Idempotency-Key": "snapshot-run"},
            json={
                "session_id": session_id,
                "input": {"text": "snapshot"},
                "response_format": {"modalities": ["text"]},
            },
        )
        assert run_response.status_code == 202, run_response.text
        run_id = run_response.json()["run_id"]
        client_id = app.state.storage.find_active_api_client_by_hash(
            hashlib.sha256(b"issue58-key").hexdigest()
        )
        destination = app.state.destination_repository.create_destination(
            session_id=session_id, api_client_id=client_id
        )
        requirements_hash = hashlib.sha256(
            b'{"items": [], "source_inputs": []}'
        ).hexdigest()
        requirements = app.state.destination_repository.create_destination_requirements(
            destination_id=destination["id"], source_inputs=[], sha256=requirements_hash
        )
        spec = app.state.destination_repository.create_destination_spec(
            destination_id=destination["id"],
            spec_version=1,
            template_id="template",
            template_version="1",
            requirements_id=requirements["requirements_id"],
            requirements_sha256=requirements_hash,
            title="海边",
            shared_environment_spec={},
            sha256="b" * 64,
        )
        app.state.destination_repository.create_scene_plan(
            destination_id=destination["id"], spec_id=spec["spec_id"], order_index=0,
            state_label="one", pet_behavior="walk", pet_emotion="happy",
            semantic_anchor="anchor-1", interaction_prompt="tap",
        )
        app.state.destination_repository.create_scene_plan(
            destination_id=destination["id"], spec_id=spec["spec_id"], order_index=1,
            state_label="two", pet_behavior="sit", pet_emotion="calm",
            semantic_anchor="anchor-2", interaction_prompt="tap",
        )
        app.state.destination_repository.seal_snapshot_for_run(
            run_id=run_id,
            destination_id=destination["id"],
            api_client_id=client_id,
            requirements_id=requirements["requirements_id"],
            spec_id=spec["spec_id"],
        )

        first = client.get(f"/api/v1/runs/{run_id}/panel-snapshot", headers=AUTH)
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["snapshot_identity"]["spec_id"] == spec["spec_id"]
        assert payload["completion"]["quality_state"] == "not_evaluated"
        assert payload["completion"]["delivery_state"] == "partial"
        etag = first.headers["etag"]
        assert len(etag) == 66

        second = client.get(
            f"/api/v1/runs/{run_id}/panel-snapshot",
            headers={**AUTH, "If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.headers["etag"] == etag
        assert second.content == b""
