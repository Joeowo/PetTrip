from __future__ import annotations

import hashlib
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_service.api.app import create_app
from agent_service.shared.config import Settings


AUTH = {"Authorization": "Bearer issue48-fixture-key"}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        service_version="0.48.0-test",
        host="127.0.0.1",
        port=8001,
        pilot_root=tmp_path,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "issue48.db",
        chat_base_url="https://fixture.invalid/v1",
        chat_api_key="fixture-chat",
        chat_model="fixture-chat-model",
        chat_timeout=1,
        chat_temperature=0,
        chat_max_tokens=256,
        pilot_api_key="issue48-fixture-key",
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


def _submit(client: TestClient, session_id: str, key: str, command: dict) -> dict:
    response = client.post(
        "/api/v1/runs",
        headers={**AUTH, "Idempotency-Key": key},
        json={"session_id": session_id, "command": command},
    )
    assert response.status_code == 202, response.text
    return response.json()


def _wait_destination(client: TestClient, destination_id: str) -> dict:
    for _ in range(100):
        dispatch = client.post(
            f"/api/v1/destinations/{destination_id}/dispatch", headers=AUTH
        )
        assert dispatch.status_code == 202, dispatch.text
        response = client.get(f"/api/v1/destinations/{destination_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["done"]:
            return payload
        time.sleep(0.01)
    raise AssertionError(
        f"destination did not reach a terminal state: {payload}; last dispatch={dispatch.json()}"
    )


def test_fixture_http_e2e_closes_and_publishes_two_immutable_scenes(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), start_worker=False)
    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", headers=AUTH)
        assert session.status_code == 201, session.text
        session_id = session.json()["session_id"]

        first = _submit(
            client,
            session_id,
            "wish-1",
            {"type": "clarification.submit_input", "input_id": "input-1", "text": "海边散步"},
        )
        second = _submit(
            client,
            session_id,
            "wish-2",
            {"type": "clarification.submit_input", "input_id": "input-2", "text": "看灯塔"},
        )
        assert first["status"] == "succeeded"
        assert second["status"] == "succeeded"

        closed = _submit(
            client,
            session_id,
            "close-1",
            {"type": "clarification.close", "close_request_id": "close-1"},
        )
        destination_id = closed["output"]["structured_data"]["destination_id"]
        manifest = _wait_destination(client, destination_id)

        assert manifest["terminal_outcome"] == "succeeded"
        assert manifest["publish_eligible"] is True
        assert manifest["scene_plans"] == [
            {"order_index": 0, "scene_id": manifest["scene_plans"][0]["scene_id"]},
            {"order_index": 1, "scene_id": manifest["scene_plans"][1]["scene_id"]},
        ]
        assert len(manifest["scene_artifacts"]) == 2
        environment_hashes = {
            artifact["shared_environment_sha256"]
            for artifact in manifest["scene_artifacts"]
        }
        assert len(environment_hashes) == 1
        for artifact in manifest["scene_artifacts"]:
            assert artifact["render_mime_type"] == "image/png"
            assert len(artifact["render_sha256"]) == 64
            artifact_response = client.get(
                f"/api/v1/destinations/{destination_id}/scenes/{artifact['scene_id']}",
                headers=AUTH,
            )
            assert artifact_response.status_code == 200, artifact_response.text
            artifact_payload = artifact_response.json()
            download = client.get(artifact_payload["download_url"], headers=AUTH)
            assert download.status_code == 200
            assert hashlib.sha256(download.content).hexdigest() == artifact["render_sha256"]

        again = client.post(
            f"/api/v1/destinations/{destination_id}/dispatch", headers=AUTH
        )
        assert again.status_code == 202
        unchanged = client.get(f"/api/v1/destinations/{destination_id}", headers=AUTH).json()
        assert unchanged["scene_artifacts"] == manifest["scene_artifacts"]
