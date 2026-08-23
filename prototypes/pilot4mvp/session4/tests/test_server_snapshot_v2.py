"""Unity 上传 v2 快照：合法放置被接受，越权修改被 4xx 拒绝。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import accepted_client, v2_payload_with_shelter  # noqa: F401


def test_v2_with_shelter_accepted_and_activates(accepted_client: TestClient) -> None:
    payload = v2_payload_with_shelter(accepted_client)
    response = accepted_client.post("/runs/session4-test-run/snapshot-v2", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["snapshot"] == "scene-snapshot-v2.json"

    active = accepted_client.get("/snapshot").json()
    assert active["schema_version"] == "0.2"
    assert active["build_slots"][0]["placed_prefab"] == "small_shelter"

    run_dir = accepted_client.app.state.run_store.state_dir / "session4-test-run"
    placement = json.loads((run_dir / "placement.json").read_text(encoding="utf-8"))
    assert placement == {"slot_id": "small_shelter", "prefab_id": "small_shelter"}


def test_v2_without_placed_prefab_rejected(accepted_client: TestClient) -> None:
    payload = accepted_client.get("/snapshot").json()
    payload["schema_version"] = "0.2"
    response = accepted_client.post("/runs/session4-test-run/snapshot-v2", json=payload)
    assert response.status_code == 422
    assert not (accepted_client.app.state.run_store.state_dir / "session4-test-run" / "scene-snapshot-v2.json").exists()


def test_v2_with_wrong_schema_version_rejected(accepted_client: TestClient) -> None:
    payload = v2_payload_with_shelter(accepted_client)
    payload["schema_version"] = "0.1"
    response = accepted_client.post("/runs/session4-test-run/snapshot-v2", json=payload)
    assert response.status_code == 422


def test_v2_with_unallowed_prefab_rejected(accepted_client: TestClient) -> None:
    payload = v2_payload_with_shelter(accepted_client)
    payload["build_slots"][0]["allowed_prefabs"] = ["something_else"]
    response = accepted_client.post("/runs/session4-test-run/snapshot-v2", json=payload)
    assert response.status_code == 422


def test_v2_changing_business_fields_rejected(accepted_client: TestClient) -> None:
    payload = v2_payload_with_shelter(accepted_client)
    payload["layers"][2]["position"]["x"] = 400
    response = accepted_client.post("/runs/session4-test-run/snapshot-v2", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "business fields" in detail


def test_v2_for_unknown_run_rejected(accepted_client: TestClient) -> None:
    payload = v2_payload_with_shelter(accepted_client)
    response = accepted_client.post("/runs/session4-nobody/snapshot-v2", json=payload)
    assert response.status_code == 404


def test_v2_extra_field_rejected(accepted_client: TestClient) -> None:
    payload = v2_payload_with_shelter(accepted_client)
    payload["build_slots"][0]["surprise"] = True
    response = accepted_client.post("/runs/session4-test-run/snapshot-v2", json=payload)
    assert response.status_code == 422
