from __future__ import annotations

import hashlib
import io
import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pilot4mvp2.agent_service.app import create_app
from pilot4mvp2.agent_service.chat_provider import ChatMessage
from pilot4mvp2.agent_service.config import Settings


class RecordingVisionProvider:
    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, messages: list[ChatMessage]) -> str:
        self.calls.append(messages)
        return "图片中有一个蓝色正方形。"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        service_version="0.2.0-test",
        host="127.0.0.1",
        port=8001,
        pilot_root=tmp_path,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "agent.db",
        chat_base_url="https://example.invalid/v1",
        chat_api_key="test-provider-key",
        chat_model="test-vision-model",
        chat_timeout=1,
        chat_temperature=0,
        chat_max_tokens=32,
        pilot_api_key="pilot-test-key",
        worker_poll_interval=0.01,
        max_text_chars=100,
    )


def _auth(key: str = "pilot-test-key") -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _image_bytes(image_format: str, size: tuple[int, int] = (32, 24)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(0, 80, 255)).save(buffer, format=image_format)
    return buffer.getvalue()


def _upload(
    client: TestClient,
    *,
    filename: str,
    content: bytes,
    mime_type: str,
    purpose: str = "vision_input",
):
    return client.post(
        "/api/v1/files",
        headers=_auth(),
        data={"purpose": purpose},
        files={"file": (filename, content, mime_type)},
    )


def _wait_for_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}", headers=_auth())
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("Run 未在测试时限内进入终态")


def test_app_startup_removes_untracked_input_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    orphan = settings.data_dir / "files" / "input" / "orphan.png"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(_image_bytes("PNG"))

    app = create_app(
        settings=settings,
        provider=RecordingVisionProvider(),
        start_worker=False,
    )
    with TestClient(app):
        assert not orphan.exists()


def test_upload_rejects_auth_and_request_size_before_multipart_parsing(
    tmp_path: Path,
) -> None:
    provider = RecordingVisionProvider()
    settings = replace(_settings(tmp_path), max_upload_bytes=100)
    app = create_app(settings=settings, provider=provider, start_worker=False)
    oversized_body = b"x" * (settings.max_upload_bytes + 64 * 1024 + 1)

    with TestClient(app) as client:
        missing_auth = client.post(
            "/api/v1/files",
            content=oversized_body,
            headers={"Content-Type": "multipart/form-data; boundary=test"},
        )
        assert missing_auth.status_code == 401
        assert missing_auth.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        too_large = client.post(
            "/api/v1/files",
            content=oversized_body,
            headers={
                **_auth(),
                "Content-Type": "multipart/form-data; boundary=test",
            },
        )
        assert too_large.status_code == 400
        assert too_large.json()["error"]["code"] == "FILE_TOO_LARGE"
        assert provider.calls == []
        assert app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0] == 0


def test_png_upload_file_id_run_and_download(tmp_path: Path) -> None:
    provider = RecordingVisionProvider()
    settings = _settings(tmp_path)
    app = create_app(settings=settings, provider=provider)
    png = _image_bytes("PNG")

    with TestClient(app) as client:
        uploaded = _upload(
            client,
            filename="blue-square.png",
            content=png,
            mime_type="image/png",
        )
        assert uploaded.status_code == 201
        metadata = uploaded.json()
        file_id = metadata["file_id"]
        assert file_id.startswith("file_")
        assert metadata["source"] == "user_upload"
        assert metadata["purpose"] == "vision_input"
        assert metadata["mime_type"] == "image/png"
        assert metadata["size_bytes"] == len(png)
        assert metadata["sha256"] == hashlib.sha256(png).hexdigest()
        assert (metadata["width"], metadata["height"]) == (32, 24)
        assert metadata["download_url"] == f"/api/v1/files/{file_id}/content"
        assert "rel_path" not in metadata
        assert "request_id" in metadata

        fetched = client.get(f"/api/v1/files/{file_id}", headers=_auth())
        downloaded = client.get(f"/api/v1/files/{file_id}/content", headers=_auth())
        assert fetched.status_code == 200
        assert fetched.json()["sha256"] == metadata["sha256"]
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"] == "image/png"
        assert downloaded.content == png

        session_id = client.post("/api/v1/sessions", headers=_auth()).json()[
            "session_id"
        ]
        run = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "vision-run-1"},
            json={
                "session_id": session_id,
                "input": {
                    "text": "图片中主要是什么颜色和形状？",
                    "attachments": [
                        {"file_id": file_id, "purpose": "vision_input"}
                    ],
                },
                "response_format": {"modalities": ["text"]},
            },
        )
        assert run.status_code == 202
        relation = app.state.storage._conn.execute(
            "SELECT mf.role, mf.file_id FROM message_files mf "
            "JOIN messages m ON m.id = mf.message_id WHERE m.run_id = ?",
            (run.json()["run_id"],),
        ).fetchall()
        assert [(row["role"], row["file_id"]) for row in relation] == [
            ("input", file_id)
        ]
        terminal = _wait_for_terminal(client, run.json()["run_id"])
        assert terminal["status"] == "succeeded"
        assert terminal["output"]["text"] == "图片中有一个蓝色正方形。"
        assert len(provider.calls) == 1
        assert provider.calls[0][-1].content == "图片中主要是什么颜色和形状？"
        assert len(provider.calls[0][-1].images) == 1
        assert provider.calls[0][-1].images[0].mime_type == "image/png"
        assert provider.calls[0][-1].images[0].data == png

        file_row = app.state.storage._conn.execute(
            "SELECT * FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        assert file_row is not None
        assert all(not isinstance(value, bytes) for value in tuple(file_row))
        assert not Path(file_row["rel_path"]).is_absolute()
        stored_path = settings.data_dir / file_row["rel_path"]
        assert stored_path.read_bytes() == png
        assert png not in settings.db_path.read_bytes()


def test_jpeg_upload_is_supported(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(tmp_path),
        provider=RecordingVisionProvider(),
        start_worker=False,
    )
    jpeg = _image_bytes("JPEG")

    with TestClient(app) as client:
        uploaded = _upload(
            client,
            filename="blue-square.jpg",
            content=jpeg,
            mime_type="image/jpeg",
            purpose="reference_image",
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["mime_type"] == "image/jpeg"
        assert uploaded.json()["purpose"] == "reference_image"


def test_disguised_extension_is_rejected_before_model_call(tmp_path: Path) -> None:
    provider = RecordingVisionProvider()
    app = create_app(settings=_settings(tmp_path), provider=provider)
    jpeg = _image_bytes("JPEG")

    with TestClient(app) as client:
        response = _upload(
            client,
            filename="disguised.png",
            content=jpeg,
            mime_type="image/png",
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FILE_TYPE_UNSUPPORTED"
        assert provider.calls == []
        count = app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]
        assert count == 0


def test_oversized_image_is_rejected_before_model_call(tmp_path: Path) -> None:
    provider = RecordingVisionProvider()
    settings = replace(_settings(tmp_path), max_upload_bytes=100)
    app = create_app(settings=settings, provider=provider)
    png = _image_bytes("PNG", size=(128, 128))
    assert len(png) > settings.max_upload_bytes

    with TestClient(app) as client:
        response = _upload(
            client,
            filename="too-large.png",
            content=png,
            mime_type="image/png",
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
        assert provider.calls == []
        count = app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]
        assert count == 0


def test_decode_pixel_and_purpose_failures_are_rejected(tmp_path: Path) -> None:
    provider = RecordingVisionProvider()
    settings = replace(_settings(tmp_path), max_image_pixels=100)
    app = create_app(settings=settings, provider=provider, start_worker=False)

    with TestClient(app) as client:
        broken = _upload(
            client,
            filename="broken.png",
            content=b"not-a-real-png",
            mime_type="image/png",
        )
        assert broken.status_code == 400
        assert broken.json()["error"]["code"] == "FILE_DECODE_FAILED"

        too_many_pixels = _upload(
            client,
            filename="too-many-pixels.png",
            content=_image_bytes("PNG", size=(11, 10)),
            mime_type="image/png",
        )
        assert too_many_pixels.status_code == 400
        assert too_many_pixels.json()["error"]["code"] == "FILE_TOO_LARGE"

        valid = _upload(
            client,
            filename="purpose.png",
            content=_image_bytes("PNG", size=(10, 10)),
            mime_type="image/png",
            purpose="reference_image",
        )
        session_id = client.post("/api/v1/sessions", headers=_auth()).json()[
            "session_id"
        ]
        attachment = {
            "file_id": valid.json()["file_id"],
            "purpose": "vision_input",
        }
        wrong_purpose = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "wrong-purpose"},
            json={
                "session_id": session_id,
                "input": {"text": "描述图片", "attachments": [attachment]},
                "response_format": {"modalities": ["text"]},
            },
        )
        assert wrong_purpose.status_code == 404
        assert wrong_purpose.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

        duplicate = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "duplicate-file"},
            json={
                "session_id": session_id,
                "input": {
                    "text": "描述图片",
                    "attachments": [
                        {**attachment, "purpose": "reference_image"},
                        {**attachment, "purpose": "reference_image"},
                    ],
                },
                "response_format": {"modalities": ["text"]},
            },
        )
        assert duplicate.status_code == 400
        assert duplicate.json()["error"]["code"] == "VALIDATION_ERROR"
        assert provider.calls == []


def test_run_rejects_attachment_total_over_upload_limit(tmp_path: Path) -> None:
    provider = RecordingVisionProvider()
    settings = replace(_settings(tmp_path), max_upload_bytes=100)
    app = create_app(settings=settings, provider=provider, start_worker=False)
    first_png = _image_bytes("PNG", size=(10, 10))
    second_png = _image_bytes("PNG", size=(10, 10))
    assert len(first_png) < 100
    assert len(first_png) + len(second_png) > 100

    with TestClient(app) as client:
        first = _upload(
            client,
            filename="first.png",
            content=first_png,
            mime_type="image/png",
        )
        second = _upload(
            client,
            filename="second.png",
            content=second_png,
            mime_type="image/png",
        )
        session_id = client.post("/api/v1/sessions", headers=_auth()).json()[
            "session_id"
        ]
        run = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "total-too-large"},
            json={
                "session_id": session_id,
                "input": {
                    "text": "比较两张图片",
                    "attachments": [
                        {"file_id": first.json()["file_id"], "purpose": "vision_input"},
                        {"file_id": second.json()["file_id"], "purpose": "vision_input"},
                    ],
                },
                "response_format": {"modalities": ["text"]},
            },
        )
        assert run.status_code == 400
        assert run.json()["error"]["code"] == "FILE_TOO_LARGE"
        assert provider.calls == []
        assert app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM runs"
        ).fetchone()[0] == 0


def test_file_and_attachment_ownership_are_enforced(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(tmp_path),
        provider=RecordingVisionProvider(),
        start_worker=False,
    )
    png = _image_bytes("PNG")

    with TestClient(app) as client:
        uploaded = _upload(
            client,
            filename="owned.png",
            content=png,
            mime_type="image/png",
        )
        file_id = uploaded.json()["file_id"]
        other_client_id = app.state.storage.upsert_api_client(
            "other-client-hash", "other-client"
        )
        app.state.storage._conn.execute(
            "UPDATE files SET api_client_id = ? WHERE id = ?",
            (other_client_id, file_id),
        )

        metadata = client.get(f"/api/v1/files/{file_id}", headers=_auth())
        content = client.get(f"/api/v1/files/{file_id}/content", headers=_auth())
        assert metadata.status_code == content.status_code == 404

        session_id = client.post("/api/v1/sessions", headers=_auth()).json()[
            "session_id"
        ]
        run = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "foreign-file"},
            json={
                "session_id": session_id,
                "input": {
                    "text": "描述图片",
                    "attachments": [
                        {"file_id": file_id, "purpose": "vision_input"}
                    ],
                },
                "response_format": {"modalities": ["text"]},
            },
        )
        assert run.status_code == 404
        assert run.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
