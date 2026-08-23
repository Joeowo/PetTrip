"""执行会话 5 组合输出验收并生成脱敏证据。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from PIL import Image

PILOT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PILOT_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pilot4mvp2.agent_service.app import create_app
from pilot4mvp2.agent_service.chat_provider import (
    ChatMessage,
    ChatProviderError,
)
from pilot4mvp2.agent_service.config import (
    ConfigurationError,
    Settings,
    load_settings,
)
from pilot4mvp2.agent_service.image_provider import (
    ImageGenerationRequest,
    ImageProviderError,
    ImageResult,
)
from pilot4mvp2.api_test_client.combined_output import read_combined_output

EVIDENCE_ROOT = PILOT_ROOT / "runs" / "pilot-multimodal-agent-session5-001"
EXPECTED_IMAGE_MODEL = "gpt-image-2"
TARGET_SIZE = (64, 48)
EXPECTED_EVENTS = [
    "run.queued",
    "run.started",
    "image_generation.started",
    "artifact.created",
    "message.created",
    "run.completed",
]
VALID_SCENE_DRAFT = {
    "type": "scene_draft",
    "schema_version": "0.1",
    "title": "潮汐灯塔",
    "theme": "seaside",
    "summary": "一处可供宠物散步和观察潮汐的海边目的地。",
    "landmark_kind": "lighthouse",
}


class _ControlledChatProvider:
    def __init__(self, case: str) -> None:
        self.case = case
        self.structured_calls = 0
        self.text_calls = 0

    async def complete(self, messages: list[ChatMessage]) -> str:
        self.text_calls += 1
        if self.case == "text_failure":
            raise ChatProviderError("受控文本失败")
        return "已根据参考图生成潮汐灯塔旅行场景。"

    async def complete_structured(
        self, messages: list[ChatMessage], request: object
    ) -> str:
        self.structured_calls += 1
        if self.case == "structured_failure":
            invalid = {
                key: value
                for key, value in VALID_SCENE_DRAFT.items()
                if key != "title"
            }
            return json.dumps(invalid, ensure_ascii=False)
        return json.dumps(VALID_SCENE_DRAFT, ensure_ascii=False)


class _ControlledImageProvider:
    def __init__(self, case: str) -> None:
        self.case = case
        self.calls = 0

    async def generate(self, request: ImageGenerationRequest) -> ImageResult:
        self.calls += 1
        if self.case == "image_failure":
            raise ImageProviderError("受控图片失败")
        return ImageResult(
            data=_png_bytes((80, 60), (40, 140, 220)),
            mime_type="image/png",
            width=80,
            height=60,
        )


def _local_env() -> dict[str, str]:
    """读取本地配置别名，但不打印或持久化密钥值。"""
    values: dict[str, str] = {}
    configured_path = os.environ.get("PETTRIP_LOCAL_ENV_PATH", "").strip()
    path = Path(configured_path) if configured_path else PILOT_ROOT / ".env.local"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolved_settings(local_env: dict[str, str], pilot_key: str) -> Settings:
    overrides = {**local_env, "PILOT_API_KEY": pilot_key}
    overrides.setdefault("IMAGES_MODEL", EXPECTED_IMAGE_MODEL)
    settings = load_settings(overrides=overrides)
    if not settings.image_base_url or not settings.image_api_key:
        raise ConfigurationError("缺少可用的 Image Provider 配置。")
    if settings.image_model != EXPECTED_IMAGE_MODEL:
        raise ConfigurationError("真实验收的 IMAGES_MODEL 必须为 gpt-image-2。")
    return settings


def _assert_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex((host, port)) == 0:
            raise RuntimeError(f"端口 {host}:{port} 已被占用；未覆盖任何证据。")


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务在健康检查前退出。")
        try:
            response = httpx.get(f"{base_url}/health", timeout=1)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("服务未在 20 秒内通过健康检查。")


def _start_live_server(
    *,
    settings: Settings,
    local_env: dict[str, str],
    runtime_root: Path,
    log_path: Path,
    pilot_key: str,
) -> tuple[subprocess.Popen[str], str]:
    env = dict(os.environ)
    env.update(local_env)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "PILOT_API_KEY": pilot_key,
            "DATA_DIR": str(runtime_root),
            "DB_PATH": str(runtime_root / "agent.db"),
            "CHAT_BASE_URL": settings.chat_base_url,
            "CHAT_API_KEY": settings.chat_api_key,
            "CHAT_MODEL": settings.chat_model,
            "CHAT_TEMPERATURE": "0",
            "CHAT_MAX_TOKENS": "512",
            "IMAGES_BASE_URL": settings.image_base_url,
            "IMAGES_API_KEY": settings.image_api_key,
            "IMAGES_MODEL": EXPECTED_IMAGE_MODEL,
            "IMAGE_GENERATION_PATH": settings.image_generation_path,
            "IMAGE_CANVAS_WIDTH": str(TARGET_SIZE[0]),
            "IMAGE_CANVAS_HEIGHT": str(TARGET_SIZE[1]),
            "IMAGE_TIMEOUT": "120",
            "IMAGE_MAX_DECODED_BYTES": str(20 * 1024 * 1024),
            "IMAGE_MAX_PIXELS": str(20_000_000),
        }
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "pilot4mvp2.agent_service.run_server"],
            cwd=REPO_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    base_url = f"http://{settings.host}:{settings.port}"
    try:
        _wait_for_health(base_url, process)
    except Exception:
        _stop(process)
        raise
    return process, base_url


def _stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def _png_bytes(
    size: tuple[int, int], color: tuple[int, int, int]
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _poll_run(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    run_id: str,
    *,
    timeout_seconds: float = 240,
) -> tuple[list[str], dict[str, Any]]:
    statuses: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"{base_url}/api/v1/runs/{run_id}", headers=headers)
        response.raise_for_status()
        body = response.json()
        if not statuses or statuses[-1] != body["status"]:
            statuses.append(body["status"])
        if body["status"] in {"succeeded", "failed"}:
            return statuses, body
        time.sleep(0.5)
    raise RuntimeError("组合 Run 未在验收时限内进入终态。")


def _event_response(
    client: Any,
    base_url: str,
    headers: dict[str, str],
    run_id: str,
) -> dict[str, Any]:
    response = client.get(f"{base_url}/api/v1/runs/{run_id}/events", headers=headers)
    response.raise_for_status()
    return response.json()


def _messages_response(
    client: Any,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
) -> dict[str, Any]:
    response = client.get(
        f"{base_url}/api/v1/sessions/{session_id}/messages", headers=headers
    )
    response.raise_for_status()
    return response.json()


def _run_live_positive(
    *,
    settings: Settings,
    local_env: dict[str, str],
    runtime_root: Path,
    staging_root: Path,
    pilot_key: str,
) -> dict[str, Any]:
    raw_log = staging_root / "server" / "live.raw.log"
    process, base_url = _start_live_server(
        settings=settings,
        local_env=local_env,
        runtime_root=runtime_root,
        log_path=raw_log,
        pilot_key=pilot_key,
    )
    try:
        headers = {"Authorization": f"Bearer {pilot_key}"}
        reference = _png_bytes((32, 24), (245, 190, 80))
        with httpx.Client(timeout=20) as client:
            session = client.post(f"{base_url}/api/v1/sessions", headers=headers)
            session.raise_for_status()
            session_id = session.json()["session_id"]
            upload = client.post(
                f"{base_url}/api/v1/files",
                headers=headers,
                files={"file": ("reference.png", reference, "image/png")},
                data={"purpose": "reference_image"},
            )
            upload.raise_for_status()
            input_file = upload.json()
            prompt = (
                "根据参考图生成一个 PetTrip 海边灯塔旅行场景。"
                "结构化字段必须完整且非空，并生成一张对应图片。"
            )
            created = client.post(
                f"{base_url}/api/v1/runs",
                headers={**headers, "Idempotency-Key": secrets.token_hex(12)},
                json={
                    "session_id": session_id,
                    "input": {
                        "text": prompt,
                        "attachments": [
                            {
                                "file_id": input_file["file_id"],
                                "purpose": "reference_image",
                            }
                        ],
                    },
                    "response_format": {
                        "modalities": ["text", "structured_data", "image"],
                        "structured_output": {
                            "schema_name": "scene_draft",
                            "schema_version": "0.1",
                        },
                    },
                },
            )
            created.raise_for_status()
            run_id = created.json()["run_id"]
            statuses, terminal = _poll_run(client, base_url, headers, run_id)
            if terminal["status"] != "succeeded":
                raise RuntimeError(
                    "真实 Provider 组合正例失败："
                    f"{terminal.get('error', {}).get('code', 'unknown')}"
                )
            combined = read_combined_output(terminal)
            attachment = combined.attachments[0]
            first = client.get(f"{base_url}{attachment.download_url}", headers=headers)
            second = client.get(f"{base_url}{attachment.download_url}", headers=headers)
            first.raise_for_status()
            second.raise_for_status()
            with Image.open(io.BytesIO(first.content)) as image:
                image_format = image.format
                image_size = image.size
            messages = _messages_response(
                client, base_url, headers, session_id
            )["messages"]
            events = _event_response(client, base_url, headers, run_id)["events"]
    finally:
        _stop(process)

    output_path = staging_root / "files" / "generated-image.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(first.content)
    assistant_messages = [item for item in messages if item["role"] == "assistant"]
    if len(assistant_messages) != 1:
        raise RuntimeError("组合正例没有且仅有一个助手消息。")
    assistant = assistant_messages[0]
    artifact_event = next(
        item for item in events if item["event_type"] == "artifact.created"
    )
    message_event = next(
        item for item in events if item["event_type"] == "message.created"
    )
    association_valid = (
        assistant["run_id"] == run_id
        and assistant["content_text"] == combined.text
        and assistant["structured_data"] == combined.structured_data.model_dump()
        and len(assistant["attachments"]) == 1
        and assistant["attachments"][0]["file_id"] == attachment.file_id
        and artifact_event["payload"]["file_id"] == attachment.file_id
        and artifact_event["payload"]["message_id"]
        == message_event["payload"]["message_id"]
        == assistant["message_id"]
    )
    return {
        "provider": "real_chat_vision_and_image_providers",
        "provider_configuration_sha256": hashlib.sha256(
            (
                f"{settings.chat_base_url}\n{settings.chat_model}\n"
                f"{settings.image_base_url}\n{settings.image_model}"
            ).encode("utf-8")
        ).hexdigest(),
        "run_id": run_id,
        "session_id": session_id,
        "assistant_message_id": assistant["message_id"],
        "input_file_id": input_file["file_id"],
        "input_sha256": hashlib.sha256(reference).hexdigest(),
        "statuses_observed": statuses,
        "terminal_status": terminal["status"],
        "events": [item["event_type"] for item in events],
        "event_message_ids_match": association_valid,
        "text": combined.text,
        "structured_data": combined.structured_data.model_dump(),
        "generated_image": {
            "file_id": attachment.file_id,
            "mime_type": attachment.mime_type,
            "size_bytes": attachment.size_bytes,
            "sha256": attachment.sha256,
            "width": attachment.width,
            "height": attachment.height,
            "download_hash_matches": hashlib.sha256(first.content).hexdigest()
            == attachment.sha256,
            "repeat_download_matches": first.content == second.content,
            "saved_copy": "files/generated-image.png",
            "saved_copy_hash_matches": hashlib.sha256(output_path.read_bytes()).hexdigest()
            == attachment.sha256,
            "pillow_format": image_format,
            "pillow_size": list(image_size),
        },
        "fixed_combined_dto_valid": True,
        "same_run_and_assistant_message": association_valid,
    }


def _controlled_settings(runtime_root: Path) -> Settings:
    return Settings(
        service_version="0.5.0-controlled",
        host="127.0.0.1",
        port=8001,
        pilot_root=runtime_root,
        data_dir=runtime_root / "data",
        db_path=runtime_root / "data" / "agent.db",
        chat_base_url="https://chat.example.invalid/v1",
        chat_api_key="controlled-chat-key",
        chat_model="controlled-chat-model",
        chat_timeout=1,
        chat_temperature=0,
        chat_max_tokens=256,
        pilot_api_key="controlled-pilot-key",
        worker_poll_interval=0.01,
        max_text_chars=500,
        image_base_url="https://image.example.invalid/v1",
        image_api_key="controlled-image-key",
        image_model=EXPECTED_IMAGE_MODEL,
        image_timeout=1,
        image_request_size="1024x1024",
        image_canvas_width=TARGET_SIZE[0],
        image_canvas_height=TARGET_SIZE[1],
        image_max_decoded_bytes=2_000_000,
    )


def _wait_testclient_run(client: TestClient, run_id: str) -> dict[str, Any]:
    for _ in range(200):
        body = client.get(
            f"/api/v1/runs/{run_id}",
            headers={"Authorization": "Bearer controlled-pilot-key"},
        ).json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.01)
    raise RuntimeError("受控 Run 未在验收时限内进入终态。")


def _run_controlled_failure(case: str, runtime_root: Path) -> dict[str, Any]:
    chat_provider = _ControlledChatProvider(case)
    image_provider = _ControlledImageProvider(case)
    settings = _controlled_settings(runtime_root)
    app = create_app(
        settings=settings,
        provider=chat_provider,
        image_provider=image_provider,
    )
    headers = {"Authorization": "Bearer controlled-pilot-key"}
    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", headers=headers)
        session_id = session.json()["session_id"]
        upload = client.post(
            "/api/v1/files",
            headers=headers,
            files={
                "file": (
                    "reference.png",
                    _png_bytes((20, 16), (220, 180, 80)),
                    "image/png",
                )
            },
            data={"purpose": "reference_image"},
        )
        created = client.post(
            "/api/v1/runs",
            headers={**headers, "Idempotency-Key": f"controlled-{case}"},
            json={
                "session_id": session_id,
                "input": {
                    "text": f"受控组合失败：{case}",
                    "attachments": [
                        {
                            "file_id": upload.json()["file_id"],
                            "purpose": "reference_image",
                        }
                    ],
                },
                "response_format": {
                    "modalities": ["text", "structured_data", "image"],
                    "structured_output": {
                        "schema_name": "scene_draft",
                        "schema_version": "0.1",
                    },
                },
            },
        )
        run_id = created.json()["run_id"]
        terminal = _wait_testclient_run(client, run_id)
        messages = _messages_response(client, "", headers, session_id)["messages"]
        events = _event_response(client, "", headers, run_id)["events"]
        generated_files = app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM files WHERE source = 'agent_generated'"
        ).fetchone()[0]
        output_relations = app.state.storage._conn.execute(
            "SELECT COUNT(*) FROM message_files WHERE role = 'output'"
        ).fetchone()[0]
    generated_paths = list((settings.data_dir / "files" / "generated").glob("*"))
    return {
        "case": case,
        "terminal_status": terminal["status"],
        "error": terminal.get("error"),
        "output_present": "output" in terminal,
        "message_roles": [item["role"] for item in messages],
        "assistant_messages_persisted": sum(
            item["role"] == "assistant" for item in messages
        ),
        "generated_file_records": generated_files,
        "output_relations": output_relations,
        "generated_files_on_disk": len(generated_paths),
        "events": [item["event_type"] for item in events],
        "provider_calls": {
            "structured": chat_provider.structured_calls,
            "text": chat_provider.text_calls,
            "image": image_provider.calls,
        },
    }


def _verify_sqlite_association(db_path: Path, live: dict[str, Any]) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT m.id, m.run_id, m.content_text, m.structured_data, f.id "
            "FROM messages m "
            "JOIN message_files mf ON mf.message_id = m.id AND mf.role = 'output' "
            "JOIN files f ON f.id = mf.file_id "
            "WHERE m.id = ? AND m.role = 'assistant'",
            (live["assistant_message_id"],),
        ).fetchone()
    finally:
        connection.close()
    return bool(
        row
        and row[0] == live["assistant_message_id"]
        and row[1] == live["run_id"]
        and row[2] == live["text"]
        and json.loads(row[3]) == live["structured_data"]
        and row[4] == live["generated_image"]["file_id"]
    )


def _legal_status_sequence(statuses: list[str], terminal: str) -> bool:
    order = {"queued": 0, "running": 1, "succeeded": 2, "failed": 2}
    return bool(statuses) and statuses[-1] == terminal and all(
        current in order
        and (index == 0 or order[current] >= order[statuses[index - 1]])
        for index, current in enumerate(statuses)
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _redact_log(
    raw_log: Path,
    destination: Path,
    forbidden_values: list[str],
    runtime_root: Path,
) -> None:
    text = raw_log.read_text(encoding="utf-8", errors="replace")
    for value in [str(REPO_ROOT), str(runtime_root), *forbidden_values]:
        if value:
            text = text.replace(value, "<redacted>")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    raw_log.unlink()


def _scan_evidence(root: Path, forbidden_values: list[str]) -> None:
    markers = [
        "Authorization: Bearer",
        "data:image/",
        str(REPO_ROOT),
        *[value for value in forbidden_values if value],
    ]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in markers:
            if marker and marker in text:
                raise RuntimeError(f"证据脱敏扫描失败：{path.name}")


def _sha256(path: Path) -> str:
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _implementation_versions(settings: Settings) -> str:
    paths = {
        "app_sha256": PILOT_ROOT / "agent_service" / "app.py",
        "file_storage_sha256": PILOT_ROOT / "agent_service" / "file_storage.py",
        "storage_sha256": PILOT_ROOT / "agent_service" / "storage.py",
        "worker_sha256": PILOT_ROOT / "agent_service" / "worker.py",
        "combined_output_sha256": PILOT_ROOT
        / "api_test_client"
        / "combined_output.py",
        "verify_session5_sha256": Path(__file__).resolve(),
    }
    lines = [
        f"python={sys.version.split()[0]}",
        f"service={settings.service_version}",
        f"fastapi={version('fastapi')}",
        f"pydantic={version('pydantic')}",
        f"pillow={version('Pillow')}",
        f"jsonschema={version('jsonschema')}",
    ]
    lines.extend(f"{name}={_sha256(path)}" for name, path in paths.items())
    return "\n".join(lines) + "\n"


def _publish_evidence(staging_root: Path, validation: dict[str, Any]) -> bool:
    if not validation.get("passed"):
        return False
    staging_root.replace(EVIDENCE_ROOT)
    return True


def main() -> int:
    local_env = _local_env()
    pilot_key = secrets.token_urlsafe(32)
    try:
        settings = _resolved_settings(local_env, pilot_key)
    except ConfigurationError as exc:
        print(f"未执行真实验收：{exc}", file=sys.stderr)
        return 2
    if EVIDENCE_ROOT.exists():
        print(f"未执行真实验收：证据目录已存在：{EVIDENCE_ROOT.name}", file=sys.stderr)
        return 2
    _assert_port_available(settings.host, settings.port)

    staging_root = Path(
        tempfile.mkdtemp(prefix=".pilot-session5-", dir=EVIDENCE_ROOT.parent)
    )
    live_runtime = Path(tempfile.mkdtemp(prefix="pettrip-session5-live-"))
    controlled_roots = {
        case: Path(tempfile.mkdtemp(prefix=f"pettrip-session5-{case}-"))
        for case in ("structured_failure", "text_failure", "image_failure")
    }
    published = False
    try:
        live = _run_live_positive(
            settings=settings,
            local_env=local_env,
            runtime_root=live_runtime,
            staging_root=staging_root,
            pilot_key=pilot_key,
        )
        live["sqlite_association_matches_api"] = _verify_sqlite_association(
            live_runtime / "agent.db", live
        )
        failures = [
            _run_controlled_failure(case, controlled_roots[case])
            for case in ("structured_failure", "text_failure", "image_failure")
        ]
        expected_errors = {
            "structured_failure": "STRUCTURED_OUTPUT_INVALID",
            "text_failure": "CHAT_PROVIDER_UNAVAILABLE",
            "image_failure": "IMAGE_PROVIDER_UNAVAILABLE",
        }
        expected_failure_events = {
            "structured_failure": ["run.queued", "run.started", "run.failed"],
            "text_failure": ["run.queued", "run.started", "run.failed"],
            "image_failure": [
                "run.queued",
                "run.started",
                "image_generation.started",
                "run.failed",
            ],
        }
        failures_passed = all(
            item["terminal_status"] == "failed"
            and item["error"]["code"] == expected_errors[item["case"]]
            and not item["output_present"]
            and item["message_roles"] == ["user"]
            and item["assistant_messages_persisted"] == 0
            and item["generated_file_records"] == 0
            and item["output_relations"] == 0
            and item["generated_files_on_disk"] == 0
            and item["events"] == expected_failure_events[item["case"]]
            for item in failures
        )
        image = live["generated_image"]
        validation = {
            "session": 5,
            "passed": (
                live["terminal_status"] == "succeeded"
                and _legal_status_sequence(
                    live["statuses_observed"], live["terminal_status"]
                )
                and live["events"] == EXPECTED_EVENTS
                and live["fixed_combined_dto_valid"]
                and live["same_run_and_assistant_message"]
                and live["event_message_ids_match"]
                and live["sqlite_association_matches_api"]
                and image["mime_type"] == "image/png"
                and [image["width"], image["height"]] == list(TARGET_SIZE)
                and image["pillow_format"] == "PNG"
                and image["pillow_size"] == list(TARGET_SIZE)
                and image["download_hash_matches"]
                and image["repeat_download_matches"]
                and image["saved_copy_hash_matches"]
                and failures_passed
            ),
            "real_provider_positive": live["terminal_status"] == "succeeded",
            "provider_configuration_sha256": live[
                "provider_configuration_sha256"
            ],
            "same_run_and_assistant_message": live[
                "same_run_and_assistant_message"
            ],
            "client_validates_text_structured_data_and_image": True,
            "controlled_failure_cases": [item["case"] for item in failures],
            "partial_assistant_messages_committed": sum(
                item["assistant_messages_persisted"] for item in failures
            ),
        }
        _write_json(staging_root / "api-tests" / "combined-run.json", live)
        _write_json(
            staging_root / "api-tests" / "failure-cases.json",
            {"cases": failures},
        )
        _write_json(
            staging_root / "api-tests" / "association-report.json",
            {
                "run_id": live["run_id"],
                "assistant_message_id": live["assistant_message_id"],
                "generated_file_id": image["file_id"],
                "same_run_and_assistant_message": live[
                    "same_run_and_assistant_message"
                ],
                "event_message_ids_match": live["event_message_ids_match"],
                "sqlite_association_matches_api": live[
                    "sqlite_association_matches_api"
                ],
            },
        )
        _write_json(staging_root / "validation-report.json", validation)
        (staging_root / "files" / "input-image.sha256.txt").write_text(
            f"{live['input_sha256']}  generated-reference.png\n", encoding="utf-8"
        )
        (staging_root / "files" / "generated-image.sha256.txt").write_text(
            f"{image['sha256']}  generated-image.png\n", encoding="utf-8"
        )
        _write_json(
            staging_root / "files" / "generated-image-metadata.json",
            image,
        )
        (staging_root / "deployment-config.redacted.txt").write_text(
            "HOST=<local>\nPORT=<local>\nCHAT_BASE_URL=<redacted>\n"
            "CHAT_API_KEY=<redacted>\nCHAT_MODEL=<redacted>\n"
            "IMAGES_BASE_URL=<redacted>\nIMAGES_API_KEY=<redacted>\n"
            f"IMAGES_MODEL={EXPECTED_IMAGE_MODEL}\n"
            "IMAGE_GENERATION_PATH=/images/generations\n"
            "IMAGE_CANVAS_WIDTH=64\nIMAGE_CANVAS_HEIGHT=48\n"
            "PILOT_API_KEY=<ephemeral>\n",
            encoding="utf-8",
        )
        (staging_root / "versions.txt").write_text(
            _implementation_versions(settings), encoding="utf-8"
        )
        (staging_root / "README.md").write_text(
            "# Agent Service 会话 5 验收证据\n\n"
            "本目录记录一次真实参考图输入和同一 Run 的文本、`scene_draft` `0.1` "
            "结构化数据、真实生成图片组合输出。公共消息历史和持久化事件共同证明三种"
            "输出属于同一个助手消息。\n\n"
            "结构化、文本和图片阶段的受控失败例分别确认：任一输出失败时，Run 不返回"
            "部分输出，也不提交助手消息、生成文件记录或输出附件关系。\n\n"
            "证据不包含 API Key、完整鉴权头、Provider 原始响应、SQLite 文件、Base64 "
            "或服务端路径。\n",
            encoding="utf-8",
        )
        _redact_log(
            staging_root / "server" / "live.raw.log",
            staging_root / "server" / "redacted.log",
            [settings.chat_api_key, settings.image_api_key, pilot_key],
            live_runtime,
        )
        _scan_evidence(
            staging_root,
            [settings.chat_api_key, settings.image_api_key, pilot_key],
        )
        published = _publish_evidence(staging_root, validation)
        print(json.dumps(validation, ensure_ascii=False))
        return 0 if published else 1
    finally:
        if not published:
            shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(live_runtime, ignore_errors=True)
        for root in controlled_roots.values():
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
