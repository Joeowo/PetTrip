from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_service.api.auth import hash_api_key
from agent_service.adapters.llm import ChatMessage
from agent_service.storage import Storage
from agent_service.shared.structured_output import (
    StructuredOutputInvalid,
    StructuredOutputRegistry,
)
from agent_service.domain.worker import RunWorker


VALID_SCENE_DRAFT = {
    "type": "scene_draft",
    "schema_version": "0.1",
    "title": "潮汐灯塔",
    "theme": "seaside",
    "summary": "一处可供宠物散步和观察潮汐的海边目的地。",
    "landmark_kind": "lighthouse",
}


class StructuredProvider:
    def __init__(self, raw_result: str) -> None:
        self.raw_result = raw_result
        self.requests: list[object] = []
        self.message_calls: list[list[ChatMessage]] = []
        self.text_message_calls: list[list[ChatMessage]] = []
        self.text_calls = 0

    async def complete(self, messages: list[ChatMessage]) -> str:
        self.text_calls += 1
        self.text_message_calls.append(messages)
        return "已根据潮汐灯塔草案生成海边旅行建议。"

    async def complete_structured(
        self, messages: list[ChatMessage], request: object
    ) -> str:
        self.message_calls.append(messages)
        self.requests.append(request)
        return self.raw_result


def _running_structured_run(
    storage: Storage, *, schema_version: str = "0.1"
) -> tuple[str, str]:
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)
    run = storage.create_run(
        api_client_id=client_id,
        session_id=session["id"],
        request_input={"text": "生成海边场景草案"},
        response_format={
            "modalities": ["structured_data"],
            "structured_output": {
                "schema_name": "scene_draft",
                "schema_version": schema_version,
            },
        },
        idempotency_key=f"structured-{schema_version}",
        idempotency_body_hash=f"structured-hash-{schema_version}",
    )
    return client_id, run["id"]


def test_registry_returns_provider_schema_copy() -> None:
    registry = StructuredOutputRegistry()
    first = registry.request_for(schema_name="scene_draft", schema_version="0.1")
    first.json_schema["required"] = []

    second = registry.request_for(schema_name="scene_draft", schema_version="0.1")

    assert "title" in second.json_schema["required"]


def test_registry_validates_and_normalizes_scene_draft_v01() -> None:
    registry = StructuredOutputRegistry()

    result = registry.parse_and_validate(
        json.dumps(VALID_SCENE_DRAFT, ensure_ascii=False),
        schema_name="scene_draft",
        schema_version="0.1",
    )

    assert result == VALID_SCENE_DRAFT


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in VALID_SCENE_DRAFT.items() if key != "title"},
        {**VALID_SCENE_DRAFT, "type": "scene_plan"},
        {**VALID_SCENE_DRAFT, "title": "   "},
    ],
    ids=["missing-title", "wrong-type", "blank-title"],
)
def test_registry_rejects_invalid_scene_draft(payload: dict[str, str]) -> None:
    registry = StructuredOutputRegistry()

    with pytest.raises(StructuredOutputInvalid):
        registry.parse_and_validate(
            json.dumps(payload, ensure_ascii=False),
            schema_name="scene_draft",
            schema_version="0.1",
        )


def test_worker_replays_prior_structured_output_in_next_run_context(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = StructuredProvider(json.dumps(VALID_SCENE_DRAFT, ensure_ascii=False))
    client_id, first_run_id = _running_structured_run(storage)
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True
        first_run = storage.get_run(first_run_id, client_id)
        assert first_run is not None and first_run["status"] == "succeeded"

        second_run = storage.create_run(
            api_client_id=client_id,
            session_id=first_run["session_id"],
            request_input={"text": "把刚才草案的标题改成月光灯塔"},
            response_format={
                "modalities": ["structured_data"],
                "structured_output": {
                    "schema_name": "scene_draft",
                    "schema_version": "0.1",
                },
            },
            idempotency_key="structured-follow-up",
            idempotency_body_hash="structured-follow-up-hash",
        )

        assert asyncio.run(worker.process_one()) is True
        second = storage.get_run(second_run["id"], client_id)
        assert second is not None and second["status"] == "succeeded"
        replayed_messages = provider.message_calls[1]
        assert [(message.role, message.content) for message in replayed_messages] == [
            ("user", "生成海边场景草案"),
            (
                "assistant",
                json.dumps(
                    VALID_SCENE_DRAFT,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
            ("user", "把刚才草案的标题改成月光灯塔"),
        ]
    finally:
        storage.close()


def test_combined_text_is_grounded_in_validated_structured_completion(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = StructuredProvider(json.dumps(VALID_SCENE_DRAFT, ensure_ascii=False))
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)
    run = storage.create_run(
        api_client_id=client_id,
        session_id=session["id"],
        request_input={"text": "生成带确认文本的场景草案"},
        response_format={
            "modalities": ["text", "structured_data"],
            "structured_output": {
                "schema_name": "scene_draft",
                "schema_version": "0.1",
            },
        },
        idempotency_key="combined-structured",
        idempotency_body_hash="combined-structured-hash",
    )
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True

        completed = storage.get_run(run["id"], client_id)
        assert completed is not None and completed["status"] == "succeeded"
        assert completed["output_text"] == "已根据潮汐灯塔草案生成海边旅行建议。"
        assert json.loads(completed["output_structured"]) == VALID_SCENE_DRAFT
        assert provider.text_calls == 1
        assert len(provider.requests) == 1
        text_context = provider.text_message_calls[0]
        assert text_context[-2] == ChatMessage(
            role="assistant",
            content=json.dumps(
                VALID_SCENE_DRAFT,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        assert "基于上面的已校验结构化结果" in text_context[-1].content
    finally:
        storage.close()


def test_unsupported_combined_schema_fails_before_any_chat_call(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = StructuredProvider(json.dumps(VALID_SCENE_DRAFT))
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)
    run = storage.create_run(
        api_client_id=client_id,
        session_id=session["id"],
        request_input={"text": "不得调用模型"},
        response_format={
            "modalities": ["text", "structured_data"],
            "structured_output": {
                "schema_name": "scene_draft",
                "schema_version": "9.9",
            },
        },
        idempotency_key="combined-unsupported",
        idempotency_body_hash="combined-unsupported-hash",
    )
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True

        failed = storage.get_run(run["id"], client_id)
        assert failed is not None and failed["status"] == "failed"
        assert failed["error_code"] == "STRUCTURED_OUTPUT_INVALID"
        assert provider.text_calls == 0
        assert provider.requests == []
    finally:
        storage.close()


def test_unwrapped_structured_provider_failure_maps_to_chat_error(
    tmp_path: Path,
) -> None:
    class BrokenStructuredProvider(StructuredProvider):
        async def complete_structured(
            self, messages: list[ChatMessage], request: object
        ) -> str:
            raise AttributeError("adapter bug")

    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = BrokenStructuredProvider(json.dumps(VALID_SCENE_DRAFT))
    client_id, run_id = _running_structured_run(storage)
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True

        failed = storage.get_run(run_id, client_id)
        assert failed is not None and failed["status"] == "failed"
        assert failed["error_code"] == "CHAT_PROVIDER_UNAVAILABLE"
    finally:
        storage.close()


def test_internal_success_commit_failure_maps_to_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = StructuredProvider(json.dumps(VALID_SCENE_DRAFT))
    client_id, run_id = _running_structured_run(storage)
    monkeypatch.setattr(
        storage,
        "complete_run_success",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("commit failed")),
    )
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True

        failed = storage.get_run(run_id, client_id)
        assert failed is not None and failed["status"] == "failed"
        assert failed["error_code"] == "INTERNAL_ERROR"
    finally:
        storage.close()


def test_worker_persists_only_validated_structured_output(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = StructuredProvider(json.dumps(VALID_SCENE_DRAFT, ensure_ascii=False))
    client_id, run_id = _running_structured_run(storage)
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True

        run = storage.get_run(run_id, client_id)
        messages = storage.list_messages(run["session_id"], client_id)  # type: ignore[index]
        assert run is not None and run["status"] == "succeeded"
        assert json.loads(run["output_structured"]) == VALID_SCENE_DRAFT
        assert run["output_text"] is None
        assert json.loads(messages[-1]["structured_data"]) == VALID_SCENE_DRAFT
        assert messages[-1]["content_text"] is None
        assert len(provider.requests) == 1
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("raw_result", "schema_version"),
    [
        (
            json.dumps(
                {key: value for key, value in VALID_SCENE_DRAFT.items() if key != "title"},
                ensure_ascii=False,
            ),
            "0.1",
        ),
        (json.dumps({**VALID_SCENE_DRAFT, "type": "scene_plan"}), "0.1"),
        (json.dumps(VALID_SCENE_DRAFT), "9.9"),
    ],
    ids=["missing-title", "wrong-type", "unsupported-version"],
)
def test_worker_maps_invalid_structured_output_without_partial_message(
    tmp_path: Path, raw_result: str, schema_version: str
) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    provider = StructuredProvider(raw_result)
    client_id, run_id = _running_structured_run(
        storage, schema_version=schema_version
    )
    worker = RunWorker(storage=storage, provider=provider, poll_interval=0.01)
    try:
        assert asyncio.run(worker.process_one()) is True

        run = storage.get_run(run_id, client_id)
        assistant_count = storage._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE run_id = ? AND role = 'assistant'",
            (run_id,),
        ).fetchone()[0]
        assert run is not None and run["status"] == "failed"
        assert run["error_code"] == "STRUCTURED_OUTPUT_INVALID"
        assert run["output_structured"] is None
        assert assistant_count == 0
        if schema_version == "9.9":
            assert provider.requests == []
    finally:
        storage.close()
