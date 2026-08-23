"""会话2 FastAPI 内容服务测试。

覆盖: Snapshot/Manifest/PNG 的稳定 URI、PNG 可被 Pillow 重新打开、
未知 asset_id 与路径穿越被 4xx 拒绝、Snapshot 不含绝对路径。
"""

from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient
from PIL import Image

from content_service.app import create_app


def _client() -> TestClient:
    return TestClient(create_app(persist=False))


def test_health_ok():
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_run_id_endpoint():
    response = _client().get("/run-id")
    assert response.status_code == 200
    assert response.json()["run_id"].startswith("session2-")


def test_get_snapshot_ok():
    response = _client().get("/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "0.1"
    assert data["scene_id"] == "session1_beach"
    assert len(data["layers"]) == 3
    assert [layer["asset_id"] for layer in data["layers"]] == [
        "beach_background",
        "lighthouse",
        "pet",
    ]


def test_snapshot_has_no_absolute_path_on_the_wire():
    text = json.dumps(_client().get("/snapshot").json(), ensure_ascii=False)
    assert "C:" not in text
    assert ":/" not in text


def test_manifest_lists_declared_assets():
    data = _client().get("/manifest").json()
    ids = {entry["asset_id"] for entry in data["assets"]}
    assert ids == {"beach_background", "lighthouse", "pet", "small_shelter"}


def test_png_download_openable_by_pillow():
    response = _client().get("/assets/beach_background.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (512, 288)


def test_unknown_asset_returns_404():
    response = _client().get("/assets/does_not_exist.png")
    assert response.status_code == 404


def test_path_traversal_attempt_rejected():
    response = _client().get("/assets/..png")
    assert response.status_code == 404
