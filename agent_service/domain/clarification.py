"""澄清流程业务逻辑（Issue #14 T2）。"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..storage.models import (
    ClarificationAlreadyClosedError,
    InputIdConflictError,
)


def mock_classify_input(text: str) -> tuple[str, str | None]:
    """Mock 分类器（T2 阶段使用固定规则，T3 引入 LLM）。

    返回: (classification, normalized_text)
    """
    stripped = text.strip()

    if not stripped:
        return ("empty", None)

    # 简单规则：包含明确否定词视为 off_topic
    off_topic_keywords = ["不想", "不要", "算了"]
    if any(kw in stripped for kw in off_topic_keywords):
        return ("off_topic", None)

    # 简单规则：过短或无意义视为 unintelligible
    if len(stripped) < 2:
        return ("unintelligible", None)

    # 默认接受为愿望输入
    return ("accepted_wish_input", stripped)


def should_close_after_processing(
    accepted_count: int, non_accepted_count: int
) -> tuple[bool, str | None]:
    """判断处理输入后是否应该关闭澄清流程。

    返回: (should_close, close_reason)
    """
    if accepted_count >= 3:
        return (True, "accepted_wish_limit")
    if non_accepted_count >= 5:
        return (True, "non_accepted_limit")
    return (False, None)


def process_clarification_input(
    conn: sqlite3.Connection,
    db: Any,  # Database instance
    *,
    session_id: str,
    run_id: str,
    input_id: str,
    text: str,
    classified_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """处理澄清输入命令的核心逻辑。

    返回包含以下字段的字典：
    - classification: str
    - normalized_text: str | None
    - clarification_closed: bool
    - destination_id: str | None
    - close_reason: str | None

    抛出:
    - ClarificationAlreadyClosedError: 澄清已关闭
    - InputIdConflictError: input_id 冲突
    """
    # 1. 获取或创建澄清会话
    clarif_session = db.get_or_create_clarification_session(conn, session_id)

    # 2. 检查是否已关闭
    if clarif_session["clarification_closed"]:
        raise ClarificationAlreadyClosedError(
            f"澄清流程已关闭，不能提交新输入。destination_id={clarif_session['destination_id']}"
        )

    # 3. 检查 input_id 幂等性
    existing_input = db.find_clarification_input(conn, session_id, input_id)
    if existing_input is not None:
        # 检查文本是否一致
        if existing_input["raw_text"] != text:
            raise InputIdConflictError(
                f"input_id {input_id} 已用于不同的文本内容。"
            )
        # 幂等：返回原有结果
        return {
            "classification": existing_input["classification"],
            "normalized_text": existing_input["normalized_text"],
            "clarification_closed": clarif_session["clarification_closed"],
            "destination_id": clarif_session["destination_id"],
            "close_reason": clarif_session["close_reason"],
            "idempotent_replay": True,
        }

    # 4. 使用事务外已校验的 LLM 结果；测试/离线路径保留规则分类。
    if classified_result is None:
        classification, normalized_text = mock_classify_input(text)
    else:
        classification = classified_result["classification"]
        normalized_text = classified_result.get("normalized_text")

    # 5. 记录输入
    db.insert_clarification_input(
        conn,
        session_id=session_id,
        run_id=run_id,
        input_id=input_id,
        raw_text=text,
        classification=classification,
        normalized_text=normalized_text,
    )

    # 6. 更新计数器
    updated_session = db.increment_clarification_counter(
        conn, session_id, classification
    )

    # 7. 检查是否需要关闭
    should_close, close_reason = should_close_after_processing(
        updated_session["accepted_wish_count"],
        updated_session["non_accepted_count"],
    )

    if should_close:
        updated_session = db.close_clarification_session(
            conn, session_id, close_reason
        )

    return {
        "classification": classification,
        "normalized_text": normalized_text,
        "clarification_closed": bool(updated_session["clarification_closed"]),
        "destination_id": updated_session["destination_id"],
        "close_reason": updated_session["close_reason"],
        "idempotent_replay": False,
    }


def process_clarification_close(
    conn: sqlite3.Connection,
    db: Any,  # Database instance
    *,
    session_id: str,
    close_request_id: str,
) -> dict[str, Any]:
    """处理 Unity 主动关闭澄清命令。

    返回包含以下字段的字典：
    - clarification_closed: bool (always True)
    - destination_id: str
    - close_reason: str
    - idempotent_replay: bool
    """
    # 1. 获取或创建澄清会话
    clarif_session = db.get_or_create_clarification_session(conn, session_id)

    # 2. 如果已关闭，幂等返回
    if clarif_session["clarification_closed"]:
        return {
            "clarification_closed": True,
            "destination_id": clarif_session["destination_id"],
            "close_reason": clarif_session["close_reason"],
            "idempotent_replay": True,
        }

    # 3. 执行关闭
    updated_session = db.close_clarification_session(
        conn, session_id, "unity_requested"
    )

    return {
        "clarification_closed": True,
        "destination_id": updated_session["destination_id"],
        "close_reason": updated_session["close_reason"],
        "idempotent_replay": False,
    }
