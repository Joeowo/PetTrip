from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_service.api.app import create_app
from agent_service.adapters.llm import ChatMessage
from agent_service.shared.config import Settings
from agent_service.api_test_client.structured_dto import (
    SceneDraftDtoV01,
    read_scene_draft_v01,
)


VALID_SCENE_DRAFT = {
    "type": "scene_draft",
    "schema_version": "0.1",
    "title": "潮汐灯塔",
    "theme": "seaside",
    "summary": "一处可供宠物散步和观察潮汐的海边目的地。",
    "landmark_kind": "lighthouse",
}


class ApiStructuredProvider:
    def __init__(self, raw_result: str) -> None:
        self.raw_result = raw_result
        self.structured_calls = 0

    async def complete(self, messages: list[ChatMessage]) -> str:
        return json.dumps(VALID_SCENE_DRAFT, ensure_ascii=False)

    async def complete_structured(
        self, messages: list[ChatMessage], request: object
    ) -> str:
        self.structured_calls += 1
        return self.raw_result


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        service_version="0.4.0-test",
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
        chat_max_tokens=256,
        pilot_api_key="pilot-test-key",
        worker_poll_interval=0.01,
        max_text_chars=100,
    )


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer pilot-test-key"}


def _payload(session_id: str, *, schema_version: str = "0.1") -> dict:
    return {
        "session_id": session_id,
        "input": {"text": "生成海边场景草案"},
        "response_format": {
            "modalities": ["structured_data"],
            "structured_output": {
                "schema_name": "scene_draft",
                "schema_version": schema_version,
            },
        },
    }


def _wait_for_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}", headers=_auth())
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("Run 未在测试时限内进入终态")


def test_api_returns_persisted_scene_draft_to_fixed_client_dto(tmp_path: Path) -> None:
    provider = ApiStructuredProvider(json.dumps(VALID_SCENE_DRAFT, ensure_ascii=False))
    app = create_app(settings=_settings(tmp_path), provider=provider)

    with TestClient(app) as client:
        session_id = client.post("/api/v1/sessions", headers=_auth()).json()["session_id"]
        created = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "structured-api-valid"},
            json=_payload(session_id),
        )
        assert created.status_code == 202

        terminal = _wait_for_terminal(client, created.json()["run_id"])
        dto = read_scene_draft_v01(terminal)

        assert terminal["status"] == "succeeded"
        assert "text" not in terminal["output"]
        assert terminal["output"]["structured_data"] == VALID_SCENE_DRAFT
        assert dto == SceneDraftDtoV01(**VALID_SCENE_DRAFT)
        persisted = app.state.storage._conn.execute(
            "SELECT output_structured FROM runs WHERE id = ?",
            (created.json()["run_id"],),
        ).fetchone()[0]
        assert json.loads(persisted) == VALID_SCENE_DRAFT


@pytest.mark.parametrize(
    ("raw_result", "schema_version"),
    [
        (
            json.dumps(
                {key: value for key, value in VALID_SCENE_DRAFT.items() if key != "title"}
            ),
            "0.1",
        ),
        (json.dumps({**VALID_SCENE_DRAFT, "type": "scene_plan"}), "0.1"),
        (json.dumps(VALID_SCENE_DRAFT), "9.9"),
    ],
    ids=["missing-title", "wrong-type", "unsupported-version"],
)
def test_api_maps_all_invalid_cases_to_structured_output_invalid(
    tmp_path: Path, raw_result: str, schema_version: str
) -> None:
    provider = ApiStructuredProvider(raw_result)
    app = create_app(settings=_settings(tmp_path), provider=provider)

    with TestClient(app) as client:
        session_id = client.post("/api/v1/sessions", headers=_auth()).json()["session_id"]
        created = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": f"invalid-{schema_version}-{len(raw_result)}"},
            json=_payload(session_id, schema_version=schema_version),
        )
        assert created.status_code == 202

        terminal = _wait_for_terminal(client, created.json()["run_id"])
        assert terminal["status"] == "failed"
        assert terminal["error"] == {
            "code": "STRUCTURED_OUTPUT_INVALID",
            "message": "结构化输出不符合请求的 Schema。",
            "retryable": False,
        }
        assert "output" not in terminal


@pytest.mark.parametrize(
    "response_format",
    [
        {"modalities": ["structured_data"]},
        {
            "modalities": ["text"],
            "structured_output": {
                "schema_name": "scene_draft",
                "schema_version": "0.1",
            },
        },
    ],
    ids=["missing-schema-selector", "selector-without-modality"],
)
def test_api_rejects_mismatched_structured_output_request(
    tmp_path: Path, response_format: dict
) -> None:
    provider = ApiStructuredProvider(json.dumps(VALID_SCENE_DRAFT))
    app = create_app(
        settings=_settings(tmp_path), provider=provider, start_worker=False
    )

    with TestClient(app) as client:
        session_id = client.post("/api/v1/sessions", headers=_auth()).json()["session_id"]
        response = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "mismatched-structured-request"},
            json={
                "session_id": session_id,
                "input": {"text": "生成场景"},
                "response_format": response_format,
            },
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert provider.structured_calls == 0


def test_client_rejects_blank_scene_draft_fields() -> None:
    response = {
        "status": "succeeded",
        "output": {
            "structured_data": {**VALID_SCENE_DRAFT, "title": "   "},
        },
    }

    with pytest.raises(ValueError):
        read_scene_draft_v01(response)


def test_client_never_extracts_scene_draft_from_text_json() -> None:
    text_only_response = {
        "status": "succeeded",
        "output": {"text": json.dumps(VALID_SCENE_DRAFT, ensure_ascii=False)},
    }

    with pytest.raises(ValueError, match="structured_data"):
        read_scene_draft_v01(text_only_response)
