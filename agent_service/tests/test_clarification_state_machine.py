"""澄清状态机测试（issue #10 第 15.1 节）。

测试澄清输入提交、计数逻辑、封盘条件和幂等性保证。
"""

import pytest

from agent_service.storage import (
    ClarificationAlreadyClosedError,
    IdempotencyKeyReusedError,
    Storage,
)


@pytest.fixture
def storage(tmp_path):
    """创建临时 Storage 实例。"""
    db_path = tmp_path / "test.db"
    store = Storage(db_path, recover=False)
    yield store
    store.close()


@pytest.fixture
def session_id(storage):
    """创建测试会话。"""
    client_id = storage.upsert_api_client("test_hash", "test_client")
    session = storage.create_session(client_id)
    return session["id"]


def test_one_input_with_multiple_wishes_increments_once(storage, session_id):
    """测试 1: 一次输入含多个愿望，只增加一次 accepted_wish_count。"""
    result = storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-1",
        text="我想去海边看灯塔，还想吃海鲜，顺便看日落",
        classification="accepted_wish_input",
    )

    assert result["session"]["accepted_wish_count"] == 1
    assert result["session"]["non_accepted_count"] == 0
    assert not result["session"]["clarification_closed"]


def test_third_accepted_input_triggers_closure(storage, session_id):
    """测试 2: 第三次 accepted 输入处理完成后封盘。"""
    # 第一次输入
    storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-1",
        text="我想去海边",
        classification="accepted_wish_input",
    )

    # 第二次输入
    storage.submit_clarification_input(
        input_id="input-2",
        session_id=session_id,
        run_id="run-2",
        text="想看灯塔",
        classification="accepted_wish_input",
    )

    # 第三次输入应该触发封盘
    result = storage.submit_clarification_input(
        input_id="input-3",
        session_id=session_id,
        run_id="run-3",
        text="还想吃海鲜",
        classification="accepted_wish_input",
    )

    assert result["session"]["accepted_wish_count"] == 3
    assert result["session"]["clarification_closed"]
    assert result["session"]["close_reason"] == "accepted_wish_limit"
    assert result["session"]["destination_id"] is not None
    assert result["session"]["destination_id"].startswith("destination_")


def test_fifth_non_accepted_input_triggers_closure(storage, session_id):
    """测试 3: 第五次 non-accepted 输入记录完成后封盘。"""
    # 提交 4 次 off_topic 输入
    for i in range(4):
        storage.submit_clarification_input(
            input_id=f"input-{i+1}",
            session_id=session_id,
            run_id=f"run-{i+1}",
            text=f"无关内容 {i+1}",
            classification="off_topic",
        )

    # 第 5 次应该触发封盘
    result = storage.submit_clarification_input(
        input_id="input-5",
        session_id=session_id,
        run_id="run-5",
        text="还是无关内容",
        classification="off_topic",
    )

    assert result["session"]["non_accepted_count"] == 5
    assert result["session"]["clarification_closed"]
    assert result["session"]["close_reason"] == "non_accepted_limit"
    assert result["session"]["destination_id"] is not None


def test_empty_does_not_increment_counters(storage, session_id):
    """测试 4: empty 不增加或重置任一计数。"""
    # 先提交一个 accepted
    storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-1",
        text="我想去海边",
        classification="accepted_wish_input",
    )

    # 提交 empty
    result = storage.submit_clarification_input(
        input_id="input-2",
        session_id=session_id,
        run_id="run-2",
        text="",
        classification="empty",
    )

    # 计数不应该改变
    assert result["session"]["accepted_wish_count"] == 1
    assert result["session"]["non_accepted_count"] == 0
    assert not result["session"]["clarification_closed"]


def test_close_command_is_idempotent(storage, session_id):
    """测试 6: 重复结束命令幂等返回同一 destination_id。"""
    # 第一次关闭
    result1 = storage.close_clarification(
        session_id=session_id,
        close_request_id="close-1",
    )

    destination_id_1 = result1["destination_id"]
    assert result1["clarification_closed"]
    assert result1["close_reason"] == "unity_requested"
    assert destination_id_1 is not None

    # 第二次关闭应该返回相同的状态
    result2 = storage.close_clarification(
        session_id=session_id,
        close_request_id="close-2",
    )

    assert result2["destination_id"] == destination_id_1
    assert result2["clarification_closed"]
    assert result2["close_reason"] == "unity_requested"


def test_new_text_after_closure_is_rejected(storage, session_id):
    """测试 7: 封盘后拒绝新文本。"""
    # 先关闭澄清
    storage.close_clarification(
        session_id=session_id,
        close_request_id="close-1",
    )

    # 尝试提交新输入应该失败
    with pytest.raises(ClarificationAlreadyClosedError):
        storage.submit_clarification_input(
            input_id="input-after-close",
            session_id=session_id,
            run_id="run-after-close",
            text="想去海边",
            classification="accepted_wish_input",
        )


def test_closure_and_destination_creation_are_atomic(storage, session_id):
    """测试 8: 封盘事务与 destination 创建不可分割。"""
    # 提交第 3 次 accepted 输入触发封盘
    storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-1",
        text="第一次",
        classification="accepted_wish_input",
    )
    storage.submit_clarification_input(
        input_id="input-2",
        session_id=session_id,
        run_id="run-2",
        text="第二次",
        classification="accepted_wish_input",
    )
    result = storage.submit_clarification_input(
        input_id="input-3",
        session_id=session_id,
        run_id="run-3",
        text="第三次",
        classification="accepted_wish_input",
    )

    # 验证封盘和 destination_id 同时存在
    session = storage.get_clarification_session(session_id)
    assert session is not None
    assert session["clarification_closed"]
    assert session["destination_id"] is not None
    assert session["close_reason"] == "accepted_wish_limit"

    # 验证两者一致
    assert session["destination_id"] == result["session"]["destination_id"]


def test_same_input_id_different_text_returns_409(storage, session_id):
    """测试 9: 同一 input_id 不同正文返回 409。"""
    # 第一次提交
    storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-1",
        text="我想去海边",
        classification="accepted_wish_input",
    )

    # 相同 input_id 不同文本应该失败
    with pytest.raises(IdempotencyKeyReusedError):
        storage.submit_clarification_input(
            input_id="input-1",
            session_id=session_id,
            run_id="run-2",
            text="我想去山里",  # 不同文本
            classification="accepted_wish_input",
        )


def test_same_input_id_same_text_is_idempotent(storage, session_id):
    """测试幂等性: 同一 input_id + 相同正文返回原结果。"""
    # 第一次提交
    result1 = storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-1",
        text="我想去海边",
        classification="accepted_wish_input",
    )

    # 相同 input_id 和文本应该返回相同结果
    result2 = storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-2",
        text="我想去海边",  # 相同文本
        classification="accepted_wish_input",
    )

    assert result1["input"]["input_id"] == result2["input"]["input_id"]
    assert result1["input"]["raw_text"] == result2["input"]["raw_text"]
    assert result1["session"]["accepted_wish_count"] == result2["session"]["accepted_wish_count"]


def test_mixed_classifications(storage, session_id):
    """测试混合分类: accepted 和 non-accepted 独立计数。"""
    # 2 个 accepted
    storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-1",
        text="想去海边",
        classification="accepted_wish_input",
    )
    storage.submit_clarification_input(
        input_id="input-2",
        session_id=session_id,
        run_id="run-2",
        text="想看灯塔",
        classification="accepted_wish_input",
    )

    # 3 个 off_topic
    storage.submit_clarification_input(
        input_id="input-3",
        session_id=session_id,
        run_id="run-3",
        text="无关内容1",
        classification="off_topic",
    )
    storage.submit_clarification_input(
        input_id="input-4",
        session_id=session_id,
        run_id="run-4",
        text="无关内容2",
        classification="off_topic",
    )
    result = storage.submit_clarification_input(
        input_id="input-5",
        session_id=session_id,
        run_id="run-5",
        text="无关内容3",
        classification="unintelligible",
    )

    # 验证独立计数
    assert result["session"]["accepted_wish_count"] == 2
    assert result["session"]["non_accepted_count"] == 3
    assert not result["session"]["clarification_closed"]


def test_unintelligible_increments_non_accepted(storage, session_id):
    """测试 unintelligible 分类增加 non_accepted_count。"""
    result = storage.submit_clarification_input(
        input_id="input-1",
        session_id=session_id,
        run_id="run-1",
        text="@@##$$",
        classification="unintelligible",
    )

    assert result["session"]["non_accepted_count"] == 1
    assert result["session"]["accepted_wish_count"] == 0
