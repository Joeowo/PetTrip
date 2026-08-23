from __future__ import annotations

import asyncio
from pathlib import Path

from agent_service.api.auth import hash_api_key
from agent_service.adapters.llm import ChatMessage, ChatProviderError
from agent_service.storage import Storage
from agent_service.domain.worker import RunWorker


class RecordingProvider:
    def __init__(self, *, error: bool = False) -> None:
        self.error = error
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, messages: list[ChatMessage]) -> str:
        self.calls.append(messages)
        if self.error:
            raise ChatProviderError("provider unavailable")
        return "真实链路由验收脚本验证；Fake Provider 已完成文本回复。"


def _queued_run(storage: Storage) -> tuple[str, str]:
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)
    run = storage.create_run(
        api_client_id=client_id,
        session_id=session["id"],
        request_input={"text": "请确认文本能力"},
        response_format={"modalities": ["text"]},
        idempotency_key="worker-idem",
        idempotency_body_hash="worker-hash",
    )
    return client_id, run["id"]


def test_worker_completes_text_run(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = RecordingProvider()
    client_id, run_id = _queued_run(storage)
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True
        run = storage.get_run(run_id, client_id)
        assert run is not None and run["status"] == "succeeded"
        assert run["output_text"]
        assert [(message.role, message.content) for message in provider.calls[0]] == [
            ("user", "请确认文本能力")
        ]
    finally:
        storage.close()


def test_worker_maps_provider_failure(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = RecordingProvider(error=True)
    client_id, run_id = _queued_run(storage)
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True
        run = storage.get_run(run_id, client_id)
        assert run is not None and run["status"] == "failed"
        assert run["error_code"] == "CHAT_PROVIDER_UNAVAILABLE"
    finally:
        storage.close()


def test_worker_context_excludes_later_and_failed_runs(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = RecordingProvider()
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)

    def create_run(key: str, text: str) -> str:
        return storage.create_run(
            api_client_id=client_id,
            session_id=session["id"],
            request_input={"text": text},
            response_format={"modalities": ["text"]},
            idempotency_key=key,
            idempotency_body_hash=f"hash-{key}",
        )["id"]

    first_id = create_run("first", "第一问")
    second_id = create_run("second", "未来排队消息")
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True
        assert [(item.role, item.content) for item in provider.calls[0]] == [
            ("user", "第一问")
        ]

        storage.claim_next_queued_run()
        storage.mark_run_failed(
            second_id,
            error_code="CHAT_PROVIDER_UNAVAILABLE",
            error_message="失败消息不得进入后续历史。",
        )
        third_id = create_run("third", "第三问")

        assert asyncio.run(worker.process_one()) is True
        assert [(item.role, item.content) for item in provider.calls[1]] == [
            ("user", "第一问"),
            ("assistant", "真实链路由验收脚本验证；Fake Provider 已完成文本回复。"),
            ("user", "第三问"),
        ]
        assert storage.get_run(first_id, client_id)["status"] == "succeeded"  # type: ignore[index]
        assert storage.get_run(third_id, client_id)["status"] == "succeeded"  # type: ignore[index]
    finally:
        storage.close()
