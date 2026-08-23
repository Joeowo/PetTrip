"""Unity 验证报告：哈希一致性强校验，截图可重新打开，SQLite 可查询。"""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import accepted_client, make_png_base64, v2_payload_with_shelter  # noqa: F401

CHECKS = [
    {"name": "pet_wave_triggered", "passed": True, "detail": "interaction fired"},
    {"name": "shelter_placed", "passed": True, "detail": "small_shelter at slot"},
]


def _post_valid_report(client: TestClient) -> dict:
    meta = client.get("/snapshot-meta").json()
    body = {
        "run_id": "session4-test-run",
        "snapshot_sha256": meta["sha256"],
        "checks": CHECKS,
        "screenshot_png_base64": make_png_base64(),
    }
    response = client.post("/runs/session4-test-run/reports", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_report_stored_in_sqlite_and_run_dir(accepted_client: TestClient) -> None:
    body = _post_valid_report(accepted_client)
    assert body["run_id"] == "session4-test-run"

    queried = accepted_client.get("/runs/session4-test-run").json()
    reports = queried["reports"]
    assert len(reports) == 1
    assert reports[0]["snapshot_sha256"] == body["snapshot_sha256"]

    run_dir = accepted_client.app.state.run_store.state_dir / "session4-test-run"
    report_file = json.loads((run_dir / "unity-report.json").read_text(encoding="utf-8"))
    assert report_file["snapshot_sha256"] == body["snapshot_sha256"]
    assert report_file["screenshot"]["filename"].startswith("screenshots/")

    screenshot_path = run_dir / report_file["screenshot"]["filename"]
    with Image.open(screenshot_path) as image:
        assert image.format == "PNG"
        assert image.size == (8, 6)


def test_report_after_v2_uses_v2_hash(accepted_client: TestClient) -> None:
    v2 = accepted_client.post(
        "/runs/session4-test-run/snapshot-v2", json=v2_payload_with_shelter(accepted_client)
    )
    assert v2.status_code == 201
    body = _post_valid_report(accepted_client)
    meta = accepted_client.get("/snapshot-meta").json()
    assert body["snapshot_sha256"] == meta["sha256"]


def test_report_with_stale_hash_rejected(accepted_client: TestClient) -> None:
    meta = accepted_client.get("/snapshot-meta").json()
    body = {
        "run_id": "session4-test-run",
        "snapshot_sha256": "0" * 64,
        "checks": CHECKS,
        "screenshot_png_base64": make_png_base64(),
    }
    response = accepted_client.post("/runs/session4-test-run/reports", json=body)
    assert response.status_code == 409
    assert "sha256" in response.json()["detail"]
    assert meta["sha256"] != "0" * 64


def test_report_with_invalid_base64_rejected(accepted_client: TestClient) -> None:
    meta = accepted_client.get("/snapshot-meta").json()
    body = {
        "run_id": "session4-test-run",
        "snapshot_sha256": meta["sha256"],
        "checks": CHECKS,
        "screenshot_png_base64": base64.b64encode(b"not a png").decode("ascii"),
    }
    response = accepted_client.post("/runs/session4-test-run/reports", json=body)
    assert response.status_code == 422


def test_report_for_unknown_run_rejected(accepted_client: TestClient) -> None:
    meta = accepted_client.get("/snapshot-meta").json()
    body = {
        "run_id": "session4-test-run",
        "snapshot_sha256": meta["sha256"],
        "checks": CHECKS,
        "screenshot_png_base64": make_png_base64(),
    }
    assert accepted_client.post("/runs/session4-nobody/reports", json=body).status_code == 404


def test_report_missing_checks_rejected(accepted_client: TestClient) -> None:
    meta = accepted_client.get("/snapshot-meta").json()
    body = {
        "snapshot_sha256": meta["sha256"],
        "screenshot_png_base64": make_png_base64(),
    }
    assert accepted_client.post("/runs/session4-test-run/reports", json=body).status_code == 422


def test_report_with_mismatched_run_id_rejected(accepted_client: TestClient) -> None:
    meta = accepted_client.get("/snapshot-meta").json()
    body = {
        "run_id": "session4-other-run",
        "snapshot_sha256": meta["sha256"],
        "checks": CHECKS,
        "screenshot_png_base64": make_png_base64(),
    }
    response = accepted_client.post("/runs/session4-test-run/reports", json=body)
    assert response.status_code == 409
    assert "run_id" in response.json()["detail"]


def test_report_run_id_missing_rejected(accepted_client: TestClient) -> None:
    meta = accepted_client.get("/snapshot-meta").json()
    body = {
        "snapshot_sha256": meta["sha256"],
        "checks": CHECKS,
        "screenshot_png_base64": make_png_base64(),
    }
    assert accepted_client.post("/runs/session4-test-run/reports", json=body).status_code == 422
