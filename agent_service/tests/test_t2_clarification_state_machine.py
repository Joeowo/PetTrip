"""T2 澄清状态机测试（Issue #14）。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_service.api.app import create_app
from agent_service.api.auth import hash_api_key
from agent_service.shared.config import Settings
from agent_service.storage import (
    Storage,
    ClarificationAlreadyClosedError,
    InputIdConflictError,
)
from agent_service.shared.ids import new_id


@pytest.fixture
def storage(tmp_path):
    """测试用存储实例。"""
    db_path = tmp_path / "test.db"
    storage = Storage(db_path, recover=False)
    # 创建测试客户端
    from agent_service.api.auth import hash_api_key
    storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    yield storage
    storage.close()


@pytest.fixture
def session(storage):
    """测试用会话。"""
    client_id = storage.find_active_api_client_by_hash(
        hash_api_key("test-key")
    )
    return storage.create_session(client_id)


def test_one_input_with_multiple_wishes_increments_once(storage, session):
    """测试 1: 一个输入包含多个愿望，accepted_wish_count 只增加 1。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 提交一个包含多个愿望的输入
    run = storage.create_clarification_run(
        api_client_id=client_id,
        session_id=session["id"],
        command={
            "type": "clarification.submit_input",
            "input_id": "input-1",
            "text": "我想去海边，还想看灯塔，还想在沙滩上玩",
        },
        idempotency_key="key-1",
        idempotency_body_hash="hash-1",
    )

    # 验证分类为 accepted
    assert run["status"] == "succeeded"
    output = json.loads(run["output_structured"])
    assert output["classification"] == "accepted_wish_input"

    # 验证计数器只增加 1
    clarif = storage._db.get_clarification_session(session["id"])
    assert clarif["accepted_wish_count"] == 1
    assert clarif["non_accepted_count"] == 0
    assert not clarif["clarification_closed"]


def test_closure_after_third_accepted(storage, session):
    """测试 2: 第三个 accepted 输入后关闭。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 提交 3 个 accepted 输入
    for i in range(3):
        run = storage.create_clarification_run(
            api_client_id=client_id,
            session_id=session["id"],
            command={
                "type": "clarification.submit_input",
                "input_id": f"input-{i}",
                "text": f"我想去地方{i}",
            },
            idempotency_key=f"key-{i}",
            idempotency_body_hash=f"hash-{i}",
        )

    # 验证第三个输入后关闭
    output = json.loads(run["output_structured"])
    assert output["clarification_closed"]
    assert output["close_reason"] == "accepted_wish_limit"
    assert output["destination_id"] is not None

    clarif = storage._db.get_clarification_session(session["id"])
    assert clarif["accepted_wish_count"] == 3
    assert clarif["clarification_closed"]


def test_closure_after_fifth_non_accepted(storage, session):
    """测试 3: 第五个 non-accepted 输入后关闭。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 提交 5 个 off_topic 输入
    for i in range(5):
        run = storage.create_clarification_run(
            api_client_id=client_id,
            session_id=session["id"],
            command={
                "type": "clarification.submit_input",
                "input_id": f"input-{i}",
                "text": "不想去",
            },
            idempotency_key=f"key-{i}",
            idempotency_body_hash=f"hash-{i}",
        )

    # 验证第五个输入后关闭
    output = json.loads(run["output_structured"])
    assert output["clarification_closed"]
    assert output["close_reason"] == "non_accepted_limit"
    assert output["destination_id"] is not None

    clarif = storage._db.get_clarification_session(session["id"])
    assert clarif["non_accepted_count"] == 5
    assert clarif["clarification_closed"]


def test_empty_classification_no_increment(storage, session):
    """测试 4: empty 分类不增加任何计数器。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 提交空输入
    run = storage.create_clarification_run(
        api_client_id=client_id,
        session_id=session["id"],
        command={
            "type": "clarification.submit_input",
            "input_id": "input-1",
            "text": "   ",
        },
        idempotency_key="key-1",
        idempotency_body_hash="hash-1",
    )

    output = json.loads(run["output_structured"])
    assert output["classification"] == "empty"

    # 验证计数器未增加
    clarif = storage._db.get_clarification_session(session["id"])
    assert clarif["accepted_wish_count"] == 0
    assert clarif["non_accepted_count"] == 0


def test_close_command_with_text_rejected(storage, session):
    """测试 5: 关闭命令和文本同时出现被拒绝。"""
    from agent_service.api.schemas import CreateRunRequest
    from pydantic import ValidationError

    # 尝试同时发送 close 和 text
    with pytest.raises(ValidationError) as exc_info:
        CreateRunRequest(
            session_id=session["id"],
            command={
                "type": "clarification.close",
                "close_request_id": "close-1",
            },
            input={"text": "还想说点什么"},
        )

    # 验证错误信息包含预期的约束
    assert "clarification.close" in str(exc_info.value)


def test_repeated_close_commands_idempotent(storage, session):
    """测试 6: 重复的关闭命令幂等返回同一 destination_id。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 第一次关闭
    run1 = storage.create_clarification_run(
        api_client_id=client_id,
        session_id=session["id"],
        command={
            "type": "clarification.close",
            "close_request_id": "close-1",
        },
        idempotency_key="key-1",
        idempotency_body_hash="hash-1",
    )

    output1 = json.loads(run1["output_structured"])
    assert output1["clarification_closed"]
    dest_id_1 = output1["destination_id"]

    # 第二次关闭（不同 idempotency_key）
    run2 = storage.create_clarification_run(
        api_client_id=client_id,
        session_id=session["id"],
        command={
            "type": "clarification.close",
            "close_request_id": "close-2",
        },
        idempotency_key="key-2",
        idempotency_body_hash="hash-2",
    )

    output2 = json.loads(run2["output_structured"])
    assert output2["destination_id"] == dest_id_1  # 相同 destination_id


def test_new_text_after_closure_rejected(storage, session):
    """测试 7: 关闭后提交新文本被拒绝。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 先关闭
    storage.create_clarification_run(
        api_client_id=client_id,
        session_id=session["id"],
        command={
            "type": "clarification.close",
            "close_request_id": "close-1",
        },
        idempotency_key="key-close",
        idempotency_body_hash="hash-close",
    )

    # 尝试提交新输入
    with pytest.raises(ClarificationAlreadyClosedError):
        storage.create_clarification_run(
            api_client_id=client_id,
            session_id=session["id"],
            command={
                "type": "clarification.submit_input",
                "input_id": "input-1",
                "text": "我还想说点什么",
            },
            idempotency_key="key-1",
            idempotency_body_hash="hash-1",
        )


def test_closure_transaction_atomic(storage, session):
    """测试 8: 关闭事务和 destination_id 创建是原子的。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 提交 3 个 accepted 输入触发自动关闭
    for i in range(3):
        run = storage.create_clarification_run(
            api_client_id=client_id,
            session_id=session["id"],
            command={
                "type": "clarification.submit_input",
                "input_id": f"input-{i}",
                "text": f"地方{i}",
            },
            idempotency_key=f"key-{i}",
            idempotency_body_hash=f"hash-{i}",
        )

    # 验证关闭和 destination_id 同时完成
    output = json.loads(run["output_structured"])
    assert output["clarification_closed"]
    assert output["destination_id"] is not None

    clarif = storage._db.get_clarification_session(session["id"])
    assert clarif["clarification_closed"]
    assert clarif["destination_id"] == output["destination_id"]


def test_same_input_id_different_content_conflict(storage, session):
    """测试 9: 相同 input_id 不同内容返回 409。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 第一次提交
    storage.create_clarification_run(
        api_client_id=client_id,
        session_id=session["id"],
        command={
            "type": "clarification.submit_input",
            "input_id": "input-1",
            "text": "原始文本",
        },
        idempotency_key="key-1",
        idempotency_body_hash="hash-1",
    )

    # 第二次提交，相同 input_id 但不同文本
    with pytest.raises(InputIdConflictError):
        storage.create_clarification_run(
            api_client_id=client_id,
            session_id=session["id"],
            command={
                "type": "clarification.submit_input",
                "input_id": "input-1",  # 相同 input_id
                "text": "不同的文本",  # 不同内容
            },
            idempotency_key="key-2",
            idempotency_body_hash="hash-2",
        )


def _api_settings(tmp_path: Path) -> Settings:
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
        pilot_api_key="pilot-test-key",
        worker_poll_interval=0.01,
        max_text_chars=100,
    )


def test_idempotency_input_id_different_text(tmp_path: Path) -> None:
    """同一 input_id 复用不同文本时返回规范幂等错误码。"""
    app = create_app(settings=_api_settings(tmp_path), start_worker=False)
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer pilot-test-key"}
        session_id = client.post("/api/v1/sessions", headers=auth).json()["session_id"]
        base_payload = {
            "session_id": session_id,
            "command": {
                "type": "clarification.submit_input",
                "input_id": "input-1",
                "text": "原始文本",
            },
        }

        first = client.post(
            "/api/v1/runs",
            headers={**auth, "Idempotency-Key": "key-1"},
            json=base_payload,
        )
        assert first.status_code == 202

        conflict = client.post(
            "/api/v1/runs",
            headers={**auth, "Idempotency-Key": "key-2"},
            json={
                **base_payload,
                "command": {**base_payload["command"], "text": "不同的文本"},
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_same_input_id_same_content_idempotent(storage, session):
    """补充测试: 相同 input_id 相同内容幂等返回原结果。"""
    client_id = storage.find_active_api_client_by_hash(hash_api_key("test-key"))

    # 第一次提交
    run1 = storage.create_clarification_run(
        api_client_id=client_id,
        session_id=session["id"],
        command={
            "type": "clarification.submit_input",
            "input_id": "input-1",
            "text": "相同文本",
        },
        idempotency_key="key-1",
        idempotency_body_hash="hash-1",
    )

    output1 = json.loads(run1["output_structured"])

    # 第二次提交，相同 input_id 和相同文本
    run2 = storage.create_clarification_run(
        api_client_id=client_id,
        session_id=session["id"],
        command={
            "type": "clarification.submit_input",
            "input_id": "input-1",  # 相同 input_id
            "text": "相同文本",  # 相同内容
        },
        idempotency_key="key-2",
        idempotency_body_hash="hash-2",
    )

    output2 = json.loads(run2["output_structured"])

    # 应该返回相同的分类结果
    assert output2["classification"] == output1["classification"]


# 需要导入 json 用于解析
import json
