from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_service.api.app import create_app
from agent_service.adapters.llm import ChatMessage
from agent_service.shared.config import Settings


class FakeProvider:
    def __init__(self) -> None:
        self.call_count = 0

    async def complete(self, messages: list[ChatMessage]) -> str:
        self.call_count += 1
        return f"已收到：{messages[-1].content}"


def _settings(tmp_path: Path, key: str = "pilot-test-key") -> Settings:
    return Settings(
        service_version="0.1.0-test",
        host="127.0.0.1",
        port=8001,
        pilot_root=tmp_path,
        data_dir=tmp_path,
        db_path=tmp_path / "agent.db",
        chat_base_url="https://example.invalid/v1",
        chat_api_key="test-provider-key",
        chat_model="test-model",
        chat_timeout=1,
        chat_temperature=0,
        chat_max_tokens=32,
        pilot_api_key=key,
        worker_poll_interval=0.01,
        max_text_chars=100,
    )


def _auth(key: str = "pilot-test-key") -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _wait_for_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}", headers=_auth())
        body = response.json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("Run 未在测试时限内进入终态")


def test_health_and_authentication_contract(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), provider=FakeProvider())
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["service_version"] == "0.1.0-test"
        assert "request_id" in health.json()
        assert "chat" not in str(health.json()).lower()

        missing = client.post("/api/v1/sessions")
        wrong = client.post("/api/v1/sessions", headers=_auth("wrong-key"))
        assert missing.status_code == wrong.status_code == 401
        assert missing.json()["error"]["code"] == "AUTHENTICATION_FAILED"
        assert wrong.json()["error"]["code"] == "AUTHENTICATION_FAILED"
        assert missing.json()["request_id"]
        assert wrong.json()["request_id"]


def test_text_run_and_idempotency_contract(tmp_path: Path) -> None:
    provider = FakeProvider()
    app = create_app(settings=_settings(tmp_path), provider=provider)
    with TestClient(app) as client:
        session_response = client.post("/api/v1/sessions", headers=_auth())
        assert session_response.status_code == 201
        session_id = session_response.json()["session_id"]
        payload = {
            "session_id": session_id,
            "input": {"text": "请确认文本能力"},
            "response_format": {"modalities": ["text"]},
        }

        missing_idempotency = client.post("/api/v1/runs", headers=_auth(), json=payload)
        assert missing_idempotency.status_code == 400

        headers = {**_auth(), "Idempotency-Key": "idem-api-1"}
        created = client.post("/api/v1/runs", headers=headers, json=payload)
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        terminal = _wait_for_terminal(client, run_id)
        assert terminal["status"] == "succeeded"
        assert terminal["output"]["text"] == "已收到：请确认文本能力"

        repeated = client.post("/api/v1/runs", headers=headers, json=payload)
        assert repeated.status_code == 202
        assert repeated.json()["run_id"] == run_id

        explicit_empty_attachments = client.post(
            "/api/v1/runs",
            headers=headers,
            json={**payload, "input": {"text": "请确认文本能力", "attachments": []}},
        )
        assert explicit_empty_attachments.status_code == 202
        assert explicit_empty_attachments.json()["run_id"] == run_id
        assert provider.call_count == 1

        conflict_payload = {
            **payload,
            "input": {"text": "这是不同请求"},
        }
        conflict = client.post("/api/v1/runs", headers=headers, json=conflict_payload)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        assert provider.call_count == 1


def test_session_ownership_and_modality_validation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings=settings, provider=FakeProvider(), start_worker=False)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/sessions", headers=_auth()).json()["session_id"]
        base = {
            "session_id": session_id,
            "input": {"text": "hello"},
            "response_format": {"modalities": ["text", "image", "image"]},
        }
        invalid = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "invalid-modalities"},
            json=base,
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

        unknown = client.post(
            "/api/v1/runs",
            headers={**_auth(), "Idempotency-Key": "unknown-session"},
            json={
                **base,
                "session_id": "session_unknown",
                "response_format": {"modalities": ["text"]},
            },
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_framework_errors_and_openapi_security_contract(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(tmp_path), provider=FakeProvider(), start_worker=False
    )
    with TestClient(app) as client:
        missing_route = client.get("/api/v1/unknown")
        wrong_method = client.get("/api/v1/sessions", headers=_auth())

        assert missing_route.status_code == 404
        assert missing_route.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert missing_route.json()["request_id"]
        assert missing_route.headers["X-Request-ID"] == missing_route.json()["request_id"]

        assert wrong_method.status_code == 405
        assert wrong_method.json()["error"]["code"] == "VALIDATION_ERROR"
        assert wrong_method.json()["request_id"]

        openapi = client.get("/openapi.json").json()
        schemes = openapi["components"]["securitySchemes"]
        assert any(
            scheme.get("type") == "http" and scheme.get("scheme") == "bearer"
            for scheme in schemes.values()
        )
        assert openapi["paths"]["/api/v1/sessions"]["post"]["security"]
        assert "security" not in openapi["paths"]["/health"]["get"]
