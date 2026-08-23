from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

from PIL import Image

from agent_service.api.auth import hash_api_key
from agent_service.adapters.llm import ChatMessage
from agent_service.storage.files import LocalImageStorage
from agent_service.storage import Storage
from agent_service.domain.worker import RunWorker


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, messages: list[ChatMessage]) -> str:
        self.calls.append(messages)
        return "已理解当前图片。"


def _create_run(
    storage: Storage,
    *,
    client_id: str,
    session_id: str,
    key: str,
    text: str,
    attachments: list[dict[str, str]] | None = None,
) -> str:
    return storage.create_run(
        api_client_id=client_id,
        session_id=session_id,
        request_input={"text": text, "attachments": attachments or []},
        response_format={"modalities": ["text"]},
        idempotency_key=key,
        idempotency_body_hash=f"hash-{key}",
    )["id"]


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_worker_maps_tampered_local_image_to_internal_error(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    file_storage = LocalImageStorage(tmp_path / "files")
    provider = RecordingProvider()
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)
    original = _png_bytes((255, 0, 0))
    rel_path = file_storage.store_bytes("file_tampered", original)
    storage.create_file(
        file_id="file_tampered",
        api_client_id=client_id,
        source="user_upload",
        purpose="vision_input",
        mime_type="image/png",
        size_bytes=len(original),
        sha256=hashlib.sha256(original).hexdigest(),
        width=2,
        height=2,
        rel_path=rel_path,
    )
    file_storage.resolve(rel_path).write_bytes(_png_bytes((0, 0, 255)))
    run_id = _create_run(
        storage,
        client_id=client_id,
        session_id=session["id"],
        key="tampered",
        text="读取被篡改图片",
        attachments=[{"file_id": "file_tampered", "purpose": "vision_input"}],
    )
    worker = RunWorker(
        storage=storage,
        file_storage=file_storage,
        provider=provider,
        poll_interval=0.01,
    )
    try:
        assert asyncio.run(worker.process_one()) is True
        run = storage.get_run(run_id, client_id)
        assert run is not None and run["status"] == "failed"
        assert run["error_code"] == "INTERNAL_ERROR"
        assert provider.calls == []
    finally:
        storage.close()


def test_worker_only_sends_current_run_images(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "agent.db", recover=False)
    file_storage = LocalImageStorage(tmp_path / "files")
    provider = RecordingProvider()
    client_id = storage.upsert_api_client(hash_api_key("test-key"), "test-client")
    session = storage.create_session(client_id)

    first_bytes = _png_bytes((255, 0, 0))
    second_bytes = _png_bytes((0, 0, 255))
    first_path = file_storage.store_bytes("file_first", first_bytes)
    second_path = file_storage.store_bytes("file_second", second_bytes)
    storage.create_file(
        file_id="file_first",
        api_client_id=client_id,
        source="user_upload",
        purpose="vision_input",
        mime_type="image/png",
        size_bytes=len(first_bytes),
        sha256=hashlib.sha256(first_bytes).hexdigest(),
        width=2,
        height=2,
        rel_path=first_path,
    )
    storage.create_file(
        file_id="file_second",
        api_client_id=client_id,
        source="user_upload",
        purpose="vision_input",
        mime_type="image/png",
        size_bytes=len(second_bytes),
        sha256=hashlib.sha256(second_bytes).hexdigest(),
        width=2,
        height=2,
        rel_path=second_path,
    )

    first_id = _create_run(
        storage,
        client_id=client_id,
        session_id=session["id"],
        key="first",
        text="第一张图",
        attachments=[{"file_id": "file_first", "purpose": "vision_input"}],
    )
    worker = RunWorker(
        storage=storage,
        file_storage=file_storage,
        provider=provider,
        poll_interval=0.01,
    )
    try:
        assert asyncio.run(worker.process_one()) is True
        assert storage.get_run(first_id, client_id)["status"] == "succeeded"  # type: ignore[index]
        assert provider.calls[0][-1].images[0].data == first_bytes

        second_id = _create_run(
            storage,
            client_id=client_id,
            session_id=session["id"],
            key="second",
            text="第二张图",
            attachments=[{"file_id": "file_second", "purpose": "vision_input"}],
        )
        assert asyncio.run(worker.process_one()) is True
        assert storage.get_run(second_id, client_id)["status"] == "succeeded"  # type: ignore[index]

        second_call = provider.calls[1]
        assert [message.content for message in second_call] == [
            "第一张图",
            "已理解当前图片。",
            "第二张图",
        ]
        assert second_call[0].images == ()
        assert second_call[1].images == ()
        assert [image.data for image in second_call[2].images] == [second_bytes]
    finally:
        storage.close()
