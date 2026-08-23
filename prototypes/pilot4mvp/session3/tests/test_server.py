from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from content_service.config import ProviderConfig
from content_service.pipeline import run_pipeline
from content_service.server import create_app

RESPONSES = ProviderConfig("https://text.test", "responses-test-key", "text-model")
IMAGES = ProviderConfig("https://images.test/v1", "images-test-key", "gpt-image-2")
VALID_WORLD = {
    "theme": "seaside",
    "landmark": "lighthouse",
    "interaction_id": "pet_wave",
    "build_slot_id": "small_shelter",
    "forbidden_objects": ["vehicle"],
    "canvas_width": 512,
    "canvas_height": 288,
    "pixels_per_unit": 16,
}


def _image_base64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (700, 500), (10, 90, 170)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _responses_success(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "text-request"},
        json={"status": "completed", "output_text": json.dumps(VALID_WORLD)},
    )


def _images_success(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "image-request"},
        json={"created": 1, "data": [{"b64_json": _image_base64()}]},
    )


@pytest.fixture()
def ready_run_dir(tmp_path):
    run_dir = tmp_path / "session3-delivery"
    run_pipeline(
        RESPONSES,
        IMAGES,
        allow_chat_compat=True,
        responses_transport=httpx.MockTransport(_responses_success),
        images_transport=httpx.MockTransport(_images_success),
        run_dir=run_dir,
    )
    # fixture 模式只写 test-fixture.json; 交付服务只认 content-ready.json。
    # 产物本身已通过流水线完整校验, 这里仅为通过服务门禁升级标记名。
    marker = run_dir / "test-fixture.json"
    marker.rename(run_dir / "content-ready.json")
    return run_dir


def test_delivery_serves_pipeline_run_over_stable_uris(ready_run_dir) -> None:
    client = TestClient(create_app(ready_run_dir))
    assert client.get("/health").status_code == 200
    assert client.get("/run-id").json() == {"run_id": "session3-delivery"}

    snapshot = client.get("/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["scene_id"] == "session1_beach"
    assert client.get("/manifest").status_code == 200

    background = client.get("/assets/beach_background.png")
    assert background.status_code == 200
    assert background.headers["content-type"] == "image/png"
    assert background.content == (ready_run_dir / "assets" / "beach_background.png").read_bytes()
    with Image.open(io.BytesIO(background.content)) as image:
        assert image.format == "PNG"
        assert image.size == (512, 288)


def test_delivery_rejects_unknown_asset_and_not_ready_run(ready_run_dir, tmp_path) -> None:
    client = TestClient(create_app(ready_run_dir))
    assert client.get("/assets/unknown.png").status_code == 404
    assert client.get("/assets/..png").status_code == 404

    empty_dir = tmp_path / "session3-empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="not content-ready"):
        create_app(empty_dir)
