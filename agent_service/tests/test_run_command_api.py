"""API 层的 Run command 扩展测试。

测试通过 HTTP API 提交澄清输入和关闭命令。
"""

import pytest
from fastapi.testclient import TestClient

from agent_service.app import create_app
from agent_service.config import Settings


@pytest.fixture
def test_settings(tmp_path):
    """创建测试配置。"""
    return Settings(
        service_version="0.1.0-test",
        host="127.0.0.1",
        port=8001,
        pilot_root=tmp_path,
        data_dir=tmp_path,
        db_path=tmp_path / "test.db",
        pilot_api_key="test-key",
        chat_base_url="http://localhost:8000",
        chat_api_key="test",
        chat_model="gpt-4",
        chat_timeout=30,
        chat_temperature=0.7,
        chat_max_tokens=1000,
        image_base_url="http://localhost:8000",
        image_api_key="test",
        image_model="dall-e-3",
        worker_poll_interval=0.1,
        max_text_chars=1000,
    )


@pytest.fixture
def client(test_settings):
    """创建测试客户端。"""
    app = create_app(settings=test_settings, start_worker=False)
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """返回认证头。"""
    return {
        "Authorization": "Bearer test-key",
        "Idempotency-Key": "test-idem-key",
    }


@pytest.fixture
def session_id(client, auth_headers):
    """创建测试会话。"""
    response = client.post("/api/v1/sessions", headers=auth_headers)
    assert response.status_code == 201
    return response.json()["session_id"]


def test_submit_clarification_input(client, auth_headers, session_id):
    """测试提交澄清输入。"""
    response = client.post(
        "/api/v1/runs",
        headers=auth_headers,
        json={
            "session_id": session_id,
            "command": {
                "type": "clarification.submit_input",
                "input_id": "input-1",
                "text": "我想去海边看灯塔",
            },
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "succeeded"
    assert data["session_id"] == session_id
    assert "clarification_state" in data["output"]
    state = data["output"]["clarification_state"]
    assert state["accepted_wish_count"] == 1
    assert state["non_accepted_count"] == 0
    assert not state["clarification_closed"]


def test_close_clarification(client, auth_headers, session_id):
    """测试独立关闭澄清。"""
    response = client.post(
        "/api/v1/runs",
        headers={**auth_headers, "Idempotency-Key": "close-key"},
        json={
            "session_id": session_id,
            "command": {
                "type": "clarification.close",
                "close_request_id": "close-1",
            },
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "succeeded"
    state = data["output"]["clarification_state"]
    assert state["clarification_closed"]
    assert state["close_reason"] == "unity_requested"
    assert state["destination_id"] is not None


def test_submit_after_close_returns_409(client, auth_headers, session_id):
    """测试关闭后提交输入返回 409。"""
    # 先关闭
    client.post(
        "/api/v1/runs",
        headers={**auth_headers, "Idempotency-Key": "close-key"},
        json={
            "session_id": session_id,
            "command": {
                "type": "clarification.close",
                "close_request_id": "close-1",
            },
        },
    )

    # 尝试提交输入
    response = client.post(
        "/api/v1/runs",
        headers={**auth_headers, "Idempotency-Key": "input-after-close"},
        json={
            "session_id": session_id,
            "command": {
                "type": "clarification.submit_input",
                "input_id": "input-after-close",
                "text": "我想去海边",
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLARIFICATION_ALREADY_CLOSED"


def test_same_input_id_different_text_returns_409(client, auth_headers, session_id):
    """测试相同 input_id 不同文本返回 409。"""
    # 第一次提交
    client.post(
        "/api/v1/runs",
        headers={**auth_headers, "Idempotency-Key": "idem-1"},
        json={
            "session_id": session_id,
            "command": {
                "type": "clarification.submit_input",
                "input_id": "input-1",
                "text": "我想去海边",
            },
        },
    )

    # 相同 input_id 不同文本
    response = client.post(
        "/api/v1/runs",
        headers={**auth_headers, "Idempotency-Key": "idem-2"},
        json={
            "session_id": session_id,
            "command": {
                "type": "clarification.submit_input",
                "input_id": "input-1",
                "text": "我想去山里",  # 不同文本
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_traditional_input_mode_still_works(client, auth_headers, session_id):
    """测试传统 input 模式仍然工作。"""
    response = client.post(
        "/api/v1/runs",
        headers=auth_headers,
        json={
            "session_id": session_id,
            "input": {"text": "你好"},
            "response_format": {"modalities": ["text"]},
        },
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] in ["queued", "running", "succeeded"]
    assert data["session_id"] == session_id


def test_command_and_input_cannot_coexist(client, auth_headers, session_id):
    """测试 command 和 input 不能同时提供。"""
    response = client.post(
        "/api/v1/runs",
        headers=auth_headers,
        json={
            "session_id": session_id,
            "command": {
                "type": "clarification.submit_input",
                "input_id": "input-1",
                "text": "测试",
            },
            "input": {"text": "你好"},
            "response_format": {"modalities": ["text"]},
        },
    )

    assert response.status_code == 400  # Validation error
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_third_accepted_triggers_closure_via_api(client, auth_headers, session_id):
    """测试通过 API 第三次 accepted 输入触发封盘。"""
    # 三次输入
    for i in range(3):
        response = client.post(
            "/api/v1/runs",
            headers={**auth_headers, "Idempotency-Key": f"idem-{i}"},
            json={
                "session_id": session_id,
                "command": {
                    "type": "clarification.submit_input",
                    "input_id": f"input-{i}",
                    "text": f"愿望 {i+1}",
                },
            },
        )
        assert response.status_code == 202

    # 第三次应该封盘
    data = response.json()
    state = data["output"]["clarification_state"]
    assert state["clarification_closed"]
    assert state["close_reason"] == "accepted_wish_limit"
    assert state["destination_id"] is not None
