"""Run 业务逻辑 - 状态机和执行流程。"""

from __future__ import annotations

from typing import Any

from ..storage.database import Database
from ..storage.models import (
    AttachmentTooLargeError,
    FileReferenceError,
    IdempotencyKeyReusedError,
    ClarificationAlreadyClosedError,
    InputIdConflictError,
)
from ..shared.errors import SERVICE_RESTARTED
from ..shared.ids import new_id
from .clarification import (
    process_clarification_input,
    process_clarification_close,
)


def create_run(
    db: Database,
    *,
    api_client_id: str,
    session_id: str,
    request_input: dict[str, Any],
    response_format: dict[str, Any],
    idempotency_key: str,
    idempotency_body_hash: str,
    max_attachment_bytes: int | None = None,
) -> dict[str, Any]:
    """原子查找或创建 queued Run，并写入用户消息与 ``run.queued`` 事件。

    这是一个深模块：简单的接口（一次函数调用）隐藏了复杂的实现
    （幂等性检查、附件验证、消息创建、事件记录、会话更新）。
    """
    run_id = new_id("run")

    with db.transaction() as conn:
        # 幂等性检查
        existing = db.find_run_by_idempotency(api_client_id, idempotency_key)
        if existing is not None:
            if existing["idempotency_body_hash"] != idempotency_body_hash:
                raise IdempotencyKeyReusedError(idempotency_key)
            return existing

        # 创建 Run
        db.insert_run(
            conn,
            run_id=run_id,
            session_id=session_id,
            api_client_id=api_client_id,
            idempotency_key=idempotency_key,
            idempotency_body_hash=idempotency_body_hash,
            request_input=request_input,
            response_format=response_format,
        )

        # 创建用户消息
        message_id = db.insert_message(
            conn,
            session_id=session_id,
            run_id=run_id,
            role="user",
            content_text=request_input.get("text") or "",
            structured_data=None,
        )

        # 验证并关联附件
        attachment_bytes = 0
        for attachment in request_input.get("attachments") or []:
            file_row = db.get_file_for_attachment(
                conn, attachment["file_id"], api_client_id
            )
            if file_row is None or file_row["purpose"] != attachment["purpose"]:
                raise FileReferenceError(attachment["file_id"])

            attachment_bytes += file_row["size_bytes"]
            if (
                max_attachment_bytes is not None
                and attachment_bytes > max_attachment_bytes
            ):
                raise AttachmentTooLargeError(attachment_bytes)

            db.attach_file_to_message(conn, message_id, file_row["id"], "input")

        # 记录事件
        db.insert_event(
            conn,
            run_id=run_id,
            event_type="run.queued",
            payload={"idempotency_key": idempotency_key},
        )

        # 更新会话时间戳
        db.update_session_timestamp(session_id)

        # 返回创建的 Run
        row = db.get_run_in_transaction(conn, run_id)

    return row


def create_clarification_run(
    db: Database,
    *,
    api_client_id: str,
    session_id: str,
    command: dict[str, Any],
    idempotency_key: str,
    idempotency_body_hash: str,
) -> dict[str, Any]:
    """创建澄清命令的 Run（submit_input 或 close）。

    抛出:
    - IdempotencyKeyReusedError: 幂等键被不同内容复用
    - ClarificationAlreadyClosedError: 澄清已关闭
    - InputIdConflictError: input_id 冲突
    """
    run_id = new_id("run")
    command_type = command["type"]

    with db.transaction() as conn:
        # 幂等性检查
        existing = db.find_run_by_idempotency(api_client_id, idempotency_key)
        if existing is not None:
            if existing["idempotency_body_hash"] != idempotency_body_hash:
                raise IdempotencyKeyReusedError(idempotency_key)
            return existing

        # 创建 Run 记录（初始为 queued）
        db.insert_run(
            conn,
            run_id=run_id,
            session_id=session_id,
            api_client_id=api_client_id,
            idempotency_key=idempotency_key,
            idempotency_body_hash=idempotency_body_hash,
            request_input=command,
            response_format={},  # 澄清命令不需要 response_format
        )

        # 记录排队事件
        db.insert_event(
            conn,
            run_id=run_id,
            event_type="run.queued",
            payload={"idempotency_key": idempotency_key, "command_type": command_type},
        )

        # 立即标记为 running（T2 阶段同步处理）
        from ..storage.models import utcnow_iso
        now = utcnow_iso()
        conn.execute(
            "UPDATE runs SET status = 'running', started_at = ? WHERE id = ?",
            (now, run_id),
        )
        db.insert_event(
            conn, run_id=run_id, event_type="run.started", payload=None
        )

        # 处理澄清命令
        if command_type == "clarification.submit_input":
            result = process_clarification_input(
                conn,
                db,
                session_id=session_id,
                run_id=run_id,
                input_id=command["input_id"],
                text=command["text"],
            )

            # 创建用户消息（记录输入文本）
            db.insert_message(
                conn,
                session_id=session_id,
                run_id=run_id,
                role="user",
                content_text=command["text"],
                structured_data=None,
            )

            # 立即标记为成功
            output_structured = {
                "classification": result["classification"],
                "normalized_text": result["normalized_text"],
                "clarification_closed": result["clarification_closed"],
                "destination_id": result["destination_id"],
                "close_reason": result["close_reason"],
            }
            db.update_run_success(conn, run_id, None, output_structured)
            db.insert_event(
                conn, run_id=run_id, event_type="run.completed", payload=None
            )

        elif command_type == "clarification.close":
            result = process_clarification_close(
                conn,
                db,
                session_id=session_id,
                close_request_id=command["close_request_id"],
            )

            # 立即标记为成功
            output_structured = {
                "clarification_closed": result["clarification_closed"],
                "destination_id": result["destination_id"],
                "close_reason": result["close_reason"],
            }
            db.update_run_success(conn, run_id, None, output_structured)
            db.insert_event(
                conn, run_id=run_id, event_type="run.completed", payload=None
            )

        # 更新会话时间戳
        db.update_session_timestamp(session_id)

        # 返回创建的 Run
        row = db.get_run_in_transaction(conn, run_id)

    return row


def complete_run_success(
    db: Database,
    run_id: str,
    *,
    assistant_text: str | None,
    structured_data: dict[str, Any] | None = None,
    output_file: dict[str, Any] | None = None,
) -> None:
    """在单事务内写入助手消息、输出文件关系与完成事件。"""

    with db.transaction() as conn:
        run = conn.execute(
            "SELECT session_id, api_client_id FROM runs "
            "WHERE id = ? AND status = 'running'",
            (run_id,),
        ).fetchone()
        if run is None:
            raise RuntimeError("Run 必须处于 running 状态才能提交成功结果。")

        # 如果有输出文件，先插入文件记录
        if output_file is not None:
            db.insert_file(
                conn,
                file_id=output_file["id"],
                api_client_id=run["api_client_id"],
                source="agent_generated",
                purpose="generated_image",
                mime_type=output_file["mime_type"],
                size_bytes=output_file["size_bytes"],
                sha256=output_file["sha256"],
                width=output_file["width"],
                height=output_file["height"],
                rel_path=output_file["rel_path"],
            )

        # 创建助手消息
        message_id = db.insert_message(
            conn,
            session_id=run["session_id"],
            run_id=run_id,
            role="assistant",
            content_text=assistant_text,
            structured_data=structured_data,
        )

        # 关联输出文件
        if output_file is not None:
            db.attach_file_to_message(conn, message_id, output_file["id"], "output")
            db.insert_event(
                conn,
                run_id=run_id,
                event_type="artifact.created",
                payload={
                    "file_id": output_file["id"],
                    "message_id": message_id,
                },
            )

        # 记录消息创建事件
        db.insert_event(
            conn,
            run_id=run_id,
            event_type="message.created",
            payload={"role": "assistant", "message_id": message_id},
        )

        # 更新 Run 状态
        db.update_run_success(conn, run_id, assistant_text, structured_data)

        # 记录完成事件
        db.insert_event(conn, run_id=run_id, event_type="run.completed", payload=None)

        # 更新会话时间戳
        db.update_session_timestamp(run["session_id"])


def mark_run_failed(
    db: Database, run_id: str, *, error_code: str, error_message: str
) -> None:
    """标记 Run 为失败状态。"""
    with db.transaction() as conn:
        rowcount = db.update_run_failed(conn, run_id, error_code, error_message)
        if rowcount:
            db.insert_event(
                conn,
                run_id=run_id,
                event_type="run.failed",
                payload={"code": error_code},
            )


def recover_pending_runs(db: Database) -> dict[str, int]:
    """遗留 ``running`` Run 标记为 ``failed(SERVICE_RESTARTED)``；``queued`` 保留。"""
    counts = {"recovered_running": 0, "kept_queued": 0}

    running_runs = db.list_running_runs()
    counts["kept_queued"] = db.count_queued_runs()

    for row in running_runs:
        mark_run_failed(
            db,
            row["id"],
            error_code=SERVICE_RESTARTED,
            error_message="服务重启，遗留的 running Run 已终止，请重新创建。",
        )
        counts["recovered_running"] += 1

    return counts
