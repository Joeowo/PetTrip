from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent_service.api.app import create_app
from agent_service.adapters.llm import (
    ChatMessage,
    ChatProviderError,
)
from agent_service.shared.config import Settings
from agent_service.adapters.image import (
    ImageGenerationRequest,
    ImageProviderError,
    ImageResult,
)
from agent_service.api_test_client.combined_output import read_combined_output


VALID_SCENE_DRAFT = {
    "type": "scene_draft",
    "schema_version": "0.1",
    "title": "潮汐灯塔",
    "theme": "seaside",
    "summary": "一处可供宠物散步和观察潮汐的海边目的地。",
    "landmark_kind": "lighthouse",
}
ASSISTANT_TEXT = "已根据参考图生成潮汐灯塔旅行场景。"


class CombinedChatProvider:
    def __init__(
        self,
        *,
        structured_result: str | None = None,
        structured_error: Exception | None = None,
        text_error: Exception | None = None,
    ) -> None:
        self.structured_result = structured_result or json.dumps(
            VALID_SCENE_DRAFT, ensure_ascii=False
        )
        self.structured_error = structured_error
        self.text_error = text_error
        self.structured_calls: list[list[ChatMessage]] = []
        self.text_calls: list[list[ChatMessage]] = []

    async def complete(self, messages: list[ChatMessage]) -> str:
        self.text_calls.append(messages)
        if self.text_error is not None:
            raise self.text_error
        return ASSISTANT_TEXT

    async def complete_structured(
        self, messages: list[ChatMessage], request: object
    ) -> str:
        self.structured_calls.append(messages)
        if self.structured_error is not None:
            raise self.structured_error
        return self.structured_result


class CombinedImageProvider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[ImageGenerationRequest] = []

    async def generate(self, request: ImageGenerationRequest) -> ImageResult:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return _image_result((1402, 1122), color=(20, 130, 220))


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        service_version="0.5.0-test",
        host="127.0.0.1",
        port=8001,
        pilot_root=tmp_path,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "agent.db",
        chat_base_url="https://chat.example.invalid/v1",
        chat_api_key="chat-secret",
        chat_model="test-chat-model",
        chat_timeout=1,
        chat_temperature=0,
        chat_max_tokens=256,
        pilot_api_key="pilot-test-key",
        worker_poll_interval=0.01,
        max_text_chars=200,
        image_base_url="https://image.example.invalid/v1",
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


def _image_result(
    size: tuple[int, int], *, color: tuple[int, int, int]
) -> ImageResult:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return ImageResult(
        data=buffer.getvalue(),
        mime_type="image/png",
        width=size[0],
        height=size[1],
    )


def _reference_png() -> bytes:
    return _image_result((24, 16), color=(245, 190, 80)).data


def _wait_for_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(200):
        response = client.get(f"/api/v1/runs/{run_id}", headers=_auth())
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("Run 未在测试时限内进入终态")


def _create_combined_run(client: TestClient, *, idempotency_key: str) -> tuple[str, str]:
    session_response = client.post("/api/v1/sessions", headers=_auth())
    assert session_response.status_code == 201
    session_id = session_response.json()["session_id"]

    upload = client.post(
        "/api/v1/files",
        headers=_auth(),
        files={"file": ("reference.png", _reference_png(), "image/png")},
        data={"purpose": "reference_image"},
    )
    assert upload.status_code == 201
    input_file_id = upload.json()["file_id"]

    created = client.post(
        "/api/v1/runs",
        headers={**_auth(), "Idempotency-Key": idempotency_key},
        json={
            "session_id": session_id,
            "input": {
                "text": "根据参考图生成一张海边灯塔旅行场景。",
                "attachments": [
                    {
                        "file_id": input_file_id,
                        "purpose": "reference_image",
                    }
                ],
            },
            "response_format": {
                "modalities": ["text", "structured_data", "image"],
                "structured_output": {
                    "schema_name": "scene_draft",
                    "schema_version": "0.1",
                },
            },
        },
    )
    assert created.status_code == 202
    return session_id, created.json()["run_id"]


def _message_history(client: TestClient, session_id: str) -> dict:
    response = client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers=_auth(),
    )
    assert response.status_code == 200
    return response.json()


def test_combined_run_links_all_outputs_to_one_run_and_assistant_message(
    tmp_path: Path,
) -> None:
    chat_provider = CombinedChatProvider()
    image_provider = CombinedImageProvider()
    app = create_app(
        settings=_settings(tmp_path),
        provider=chat_provider,
        image_provider=image_provider,
    )

    with TestClient(app) as client:
        session_id, run_id = _create_combined_run(
            client, idempotency_key="session5-combined-success"
        )
        terminal = _wait_for_terminal(client, run_id)
        combined = read_combined_output(terminal)

        assert terminal["status"] == "succeeded"
        assert combined.text == ASSISTANT_TEXT
        assert combined.structured_data.model_dump() == VALID_SCENE_DRAFT
        assert len(combined.attachments) == 1
        attachment = combined.attachments[0]
        assert attachment.source == "agent_generated"
        assert attachment.purpose == "generated_image"
        assert attachment.mime_type == "image/png"
        assert (attachment.width, attachment.height) == (64, 48)
        assert image_provider.calls == [
            ImageGenerationRequest(prompt="根据参考图生成一张海边灯塔旅行场景。")
        ]

        assert len(chat_provider.structured_calls) == 1
        assert len(chat_provider.text_calls) == 1
        structured_user = chat_provider.structured_calls[0][-1]
        assert structured_user.role == "user"
        assert len(structured_user.images) == 1
        assert structured_user.images[0].mime_type == "image/png"
        assert structured_user.images[0].data == _reference_png()
        text_user = chat_provider.text_calls[0][0]
        assert text_user.images == structured_user.images
        assert "基于上面的已校验结构化结果" in chat_provider.text_calls[0][-1].content

        download = client.get(attachment.download_url, headers=_auth())
        assert download.status_code == 200
        assert hashlib.sha256(download.content).hexdigest() == attachment.sha256
        with Image.open(io.BytesIO(download.content)) as image:
            assert image.format == "PNG"
            assert image.size == (64, 48)

        history = _message_history(client, session_id)
        assert history["session_id"] == session_id
        assert [message["role"] for message in history["messages"]] == [
            "user",
            "assistant",
        ]
        user_message, assistant_message = history["messages"]
        assert user_message["run_id"] == assistant_message["run_id"] == run_id
        assert user_message["attachments"][0]["file_id"] != attachment.file_id
        assert user_message["attachments"][0]["purpose"] == "reference_image"
        assert assistant_message["content_text"] == combined.text
        assert assistant_message["structured_data"] == VALID_SCENE_DRAFT
        assert assistant_message["attachments"][0]["file_id"] == attachment.file_id
        assert assistant_message["attachments"][0]["purpose"] == "generated_image"

        events_response = client.get(
            f"/api/v1/runs/{run_id}/events", headers=_auth()
        )
        assert events_response.status_code == 200
        events = events_response.json()["events"]
        assert [event["event_type"] for event in events] == [
            "run.queued",
            "run.started",
            "image_generation.started",
            "artifact.created",
            "message.created",
            "run.completed",
        ]
        artifact_event = events[3]
        message_event = events[4]
        assert artifact_event["payload"]["file_id"] == attachment.file_id
        assert (
            artifact_event["payload"]["message_id"]
            == message_event["payload"]["message_id"]
            == assistant_message["message_id"]
        )


def test_message_history_returns_not_found_for_unknown_session(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), start_worker=False)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/sessions/session_missing/messages",
            headers=_auth(),
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.parametrize(
    ("chat_provider", "image_provider", "expected_code", "expected_events"),
    [
        (
            CombinedChatProvider(
                structured_result=json.dumps(
                    {
                        key: value
                        for key, value in VALID_SCENE_DRAFT.items()
                        if key != "title"
                    },
                    ensure_ascii=False,
                )
            ),
            CombinedImageProvider(),
            "STRUCTURED_OUTPUT_INVALID",
            ["run.queued", "run.started", "run.failed"],
        ),
        (
            CombinedChatProvider(
                text_error=ChatProviderError("controlled text failure")
            ),
            CombinedImageProvider(),
            "CHAT_PROVIDER_UNAVAILABLE",
            ["run.queued", "run.started", "run.failed"],
        ),
        (
            CombinedChatProvider(),
            CombinedImageProvider(
                error=ImageProviderError("controlled image failure")
            ),
            "IMAGE_PROVIDER_UNAVAILABLE",
            [
                "run.queued",
                "run.started",
                "image_generation.started",
                "run.failed",
            ],
        ),
    ],
    ids=["structured-failure", "text-failure", "image-failure"],
)
def test_combined_stage_failure_never_commits_partial_assistant_output(
    tmp_path: Path,
    chat_provider: CombinedChatProvider,
    image_provider: CombinedImageProvider,
    expected_code: str,
    expected_events: list[str],
) -> None:
    app = create_app(
        settings=_settings(tmp_path),
        provider=chat_provider,
        image_provider=image_provider,
    )

    with TestClient(app) as client:
        session_id, run_id = _create_combined_run(
            client, idempotency_key=f"session5-{expected_code.lower()}"
        )
        terminal = _wait_for_terminal(client, run_id)

        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == expected_code
        assert "output" not in terminal
        history = _message_history(client, session_id)
        assert [message["role"] for message in history["messages"]] == ["user"]
        assert app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM files WHERE source = 'agent_generated'"
        ).fetchone()[0] == 0
        assert app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM message_files WHERE role = 'output'"
        ).fetchone()[0] == 0
        assert list((_settings(tmp_path).data_dir / "files" / "generated").iterdir()) == []

        events = client.get(
            f"/api/v1/runs/{run_id}/events", headers=_auth()
        ).json()["events"]
        assert [event["event_type"] for event in events] == expected_events

    if expected_code == "STRUCTURED_OUTPUT_INVALID":
        assert len(chat_provider.structured_calls) == 1
        assert chat_provider.text_calls == []
        assert image_provider.calls == []
    elif expected_code == "CHAT_PROVIDER_UNAVAILABLE":
        assert len(chat_provider.structured_calls) == 1
        assert len(chat_provider.text_calls) == 1
        assert image_provider.calls == []
    else:
        assert len(chat_provider.structured_calls) == 1
        assert len(chat_provider.text_calls) == 1
        assert len(image_provider.calls) == 1


def test_combined_commit_failure_removes_generated_file_and_partial_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_provider = CombinedChatProvider()
    image_provider = CombinedImageProvider()
    app = create_app(
        settings=_settings(tmp_path),
        provider=chat_provider,
        image_provider=image_provider,
        start_worker=False,
    )

    def fail_commit(*args: object, **kwargs: object) -> str:
        raise RuntimeError("controlled commit failure")

    monkeypatch.setattr(app.state.storage, "complete_run_success", fail_commit)

    with TestClient(app) as client:
        session_id, run_id = _create_combined_run(
            client, idempotency_key="session5-commit-failure"
        )
        assert app.state.worker is not None
        assert __import__("asyncio").run(app.state.worker.process_one()) is True
        terminal = client.get(f"/api/v1/runs/{run_id}", headers=_auth()).json()

        assert terminal["status"] == "failed"
        assert terminal["error"]["code"] == "INTERNAL_ERROR"
        assert "output" not in terminal
        history = _message_history(client, session_id)
        assert [message["role"] for message in history["messages"]] == ["user"]
        assert app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM files WHERE source = 'agent_generated'"
        ).fetchone()[0] == 0
        assert app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM message_files WHERE role = 'output'"
        ).fetchone()[0] == 0
        assert list((_settings(tmp_path).data_dir / "files" / "generated").iterdir()) == []
