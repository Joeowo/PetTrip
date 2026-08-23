from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from agent_service.app import create_app
from agent_service.config import Settings
from agent_service.image_provider import ImageGenerationRequest, ImageResult


class RecordingImageProvider:
    def __init__(self, *, result: ImageResult | None = None, error: Exception | None = None):
        self.calls: list[ImageGenerationRequest] = []
        self.result = result
        self.error = error

    async def generate(self, request: ImageGenerationRequest) -> ImageResult:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        service_version="0.3.0-test",
        host="127.0.0.1",
        port=8001,
        pilot_root=tmp_path,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "agent.db",
        chat_base_url="https://example.invalid/v1",
        chat_api_key="chat-secret",
        chat_model="test-chat-model",
        chat_timeout=1,
        chat_temperature=0,
        chat_max_tokens=32,
        pilot_api_key="pilot-test-key",
        worker_poll_interval=0.01,
        max_text_chars=100,
        image_base_url="https://image.example/v1",
        image_api_key="image-secret",
        image_model="gpt-image-2",
        image_timeout=1,
        image_request_size="1024x1024",
        image_canvas_width=64,
        image_canvas_height=48,
        image_max_decoded_bytes=2_000_000,
    )


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer pilot-test-key"}


def _image_result(size: tuple[int, int] = (1402, 1122)) -> ImageResult:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(0, 160, 255)).save(buffer, format="PNG")
    return ImageResult(data=buffer.getvalue(), mime_type="image/png", width=size[0], height=size[1])


def _wait_for_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}", headers=_auth())
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("Run 未在测试时限内进入终态")


def test_image_run_downloads_normalized_png_with_stable_hash(tmp_path: Path) -> None:
    provider = RecordingImageProvider(result=_image_result())
    app = create_app(settings=_settings(tmp_path), image_provider=provider)

    with TestClient(app) as client:
        session_id = client.post("/api/v1/sessions", headers=_auth()).json()["session_id"]
        response = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "image-output-1"},
            json={
                "session_id": session_id,
                "input": {"text": "生成一张海边小狗图片"},
                "response_format": {"modalities": ["image"]},
            },
        )
        assert response.status_code == 202
        terminal = _wait_for_terminal(client, response.json()["run_id"])
        assert terminal["status"] == "succeeded"
        assert "output" in terminal
        assert "text" not in terminal["output"]
        assert "structured_data" not in terminal["output"]
        attachment = terminal["output"]["attachments"][0]
        assert attachment["source"] == "agent_generated"
        assert attachment["purpose"] == "generated_image"
        assert attachment["mime_type"] == "image/png"
        assert (attachment["width"], attachment["height"]) == (64, 48)
        assert "rel_path" not in attachment
        assert provider.calls[0].prompt == "生成一张海边小狗图片"

        first = client.get(attachment["download_url"], headers=_auth())
        second = client.get(attachment["download_url"], headers=_auth())
        assert first.status_code == second.status_code == 200
        assert first.content == second.content
        assert hashlib.sha256(first.content).hexdigest() == attachment["sha256"]
        with Image.open(io.BytesIO(first.content)) as image:
            assert image.format == "PNG"
            assert image.size == (64, 48)

        row = app.state.storage._conn.execute(
            "SELECT * FROM files WHERE id = ?", (attachment["file_id"],)
        ).fetchone()
        assert row is not None
        assert all(not isinstance(value, bytes) for value in tuple(row))
        assert base64.b64encode(first.content) not in _settings(tmp_path).db_path.read_bytes()

        events = client.get(
            f"/api/v1/runs/{response.json()['run_id']}/events", headers=_auth()
        )
        assert events.status_code == 200
        assert [item["event_type"] for item in events.json()["events"]] == [
            "run.queued",
            "run.started",
            "image_generation.started",
            "artifact.created",
            "message.created",
            "run.completed",
        ]


def test_image_provider_failure_leaves_no_generated_file_or_records(tmp_path: Path) -> None:
    provider = RecordingImageProvider(error=RuntimeError("provider response leaked"))
    app = create_app(settings=_settings(tmp_path), image_provider=provider)

    with TestClient(app) as client:
        session_id = client.post("/api/v1/sessions", headers=_auth()).json()["session_id"]
        created = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "image-output-failure"},
            json={
                "session_id": session_id,
                "input": {"text": "失败图片"},
                "response_format": {"modalities": ["image"]},
            },
        )
        terminal = _wait_for_terminal(client, created.json()["run_id"])
        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "IMAGE_PROVIDER_UNAVAILABLE"
        assert "provider response leaked" not in json.dumps(terminal, ensure_ascii=False)
        assert app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM files WHERE source = 'agent_generated'"
        ).fetchone()[0] == 0
        assert app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM message_files WHERE role = 'output'"
        ).fetchone()[0] == 0
        generated_dir = _settings(tmp_path).data_dir / "files" / "generated"
        assert list(generated_dir.iterdir()) == []
