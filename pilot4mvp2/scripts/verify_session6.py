"""执行会话 6 持久化与恢复验收并生成脱敏证据。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

PILOT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PILOT_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EVIDENCE_ROOT = Path(
    os.environ.get(
        "SESSION6_EVIDENCE_ROOT",
        PILOT_ROOT / "runs" / "pilot-multimodal-agent-session6-001",
    )
)
PILOT_KEY = secrets.token_urlsafe(32)
TARGET_SIZE = (64, 48)


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _start_server(
    *,
    runtime_root: Path,
    provider_delay: float,
    log_path: Path,
    call_log: Path,
) -> tuple[subprocess.Popen[str], str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "PILOT_API_KEY": PILOT_KEY,
            "SERVICE_VERSION": "0.6.0-controlled",
            "HOST": "127.0.0.1",
            "PORT": "0",
            "DATA_DIR": str(runtime_root / "data"),
            "DB_PATH": str(runtime_root / "data" / "agent.db"),
            "CHAT_BASE_URL": "https://controlled.invalid/v1",
            "CHAT_API_KEY": "controlled-chat-key",
            "CHAT_MODEL": "controlled-chat-model",
            "CHAT_TIMEOUT": "60",
            "CHAT_TEMPERATURE": "0",
            "CHAT_MAX_TOKENS": "256",
            "IMAGES_BASE_URL": "https://controlled.invalid/v1",
            "IMAGES_API_KEY": "controlled-image-key",
            "IMAGES_MODEL": "gpt-image-2",
            "IMAGE_CANVAS_WIDTH": str(TARGET_SIZE[0]),
            "IMAGE_CANVAS_HEIGHT": str(TARGET_SIZE[1]),
            "IMAGE_MAX_DECODED_BYTES": str(2_000_000),
            "IMAGE_MAX_PIXELS": str(20_000_000),
            "WORKER_POLL_INTERVAL": "0.01",
            "MAX_TEXT_CHARS": "8000",
            "MAX_UPLOAD_BYTES": str(10 * 1024 * 1024),
            "MAX_IMAGE_DIMENSION": "4096",
            "MAX_IMAGE_PIXELS": str(20_000_000),
            "IMAGE_TIMEOUT": "120",
            "IMAGE_REQUEST_SIZE": "1024x1024",
            "IMAGE_GENERATION_PATH": "/images/generations",
            "SESSION6_PROVIDER_DELAY_SECONDS": str(provider_delay),
            "SESSION6_PROVIDER_CALL_LOG": str(call_log),
            "SESSION6_PORT_FILE": str(runtime_root / "server.port"),
        }
    )
    port_file = runtime_root / "server.port"
    port_file.unlink(missing_ok=True)
    port_file.with_suffix(".tmp").unlink(missing_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "pilot4mvp2.scripts.session6_controlled_server"],
            cwd=REPO_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    base_url: str | None = None
    ready = False
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("受控服务在健康检查前退出。")
            if base_url is None and port_file.is_file():
                try:
                    port = int(port_file.read_text(encoding="ascii"))
                except (OSError, ValueError):
                    time.sleep(0.05)
                    continue
                base_url = f"http://127.0.0.1:{port}"
            if base_url is not None:
                try:
                    response = httpx.get(f"{base_url}/health", timeout=1)
                    if response.status_code == 200:
                        ready = True
                        return process, base_url
                except httpx.HTTPError:
                    pass
            time.sleep(0.1)
        raise RuntimeError("受控服务未在 20 秒内通过健康检查。")
    finally:
        if not ready:
            _stop_process(process)


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("受控服务无法在强制终止后退出。") from exc


def _kill_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("受控服务无法在强制终止后退出。") from exc


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {PILOT_KEY}"}


def _wait_for_status(
    client: httpx.Client,
    base_url: str,
    run_id: str,
    *,
    wanted: set[str],
    timeout_seconds: float = 20,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"{base_url}/api/v1/runs/{run_id}", headers=_headers()
        )
        response.raise_for_status()
        latest = response.json()
        if latest.get("status") in wanted:
            return latest
        time.sleep(0.05)
    raise RuntimeError(f"Run 未进入预期状态：{wanted}，当前={latest.get('status')}")


def _create_session(client: httpx.Client, base_url: str) -> str:
    response = client.post(f"{base_url}/api/v1/sessions", headers=_headers())
    response.raise_for_status()
    return response.json()["session_id"]


def _create_run(
    client: httpx.Client,
    base_url: str,
    *,
    session_id: str,
    text: str,
    modalities: list[str],
    idempotency_key: str,
    attachment: dict[str, str] | None = None,
) -> str:
    attachments = [attachment] if attachment is not None else []
    response_format: dict[str, Any] = {"modalities": modalities}
    if "structured_data" in modalities:
        response_format["structured_output"] = {
            "schema_name": "scene_draft",
            "schema_version": "0.1",
        }
    response = client.post(
        f"{base_url}/api/v1/runs",
        headers={**_headers(), "Idempotency-Key": idempotency_key},
        json={
            "session_id": session_id,
            "input": {"text": text, "attachments": attachments},
            "response_format": response_format,
        },
    )
    response.raise_for_status()
    return response.json()["run_id"]


def _get_messages(client: httpx.Client, base_url: str, session_id: str) -> dict[str, Any]:
    response = client.get(
        f"{base_url}/api/v1/sessions/{session_id}/messages", headers=_headers()
    )
    response.raise_for_status()
    return response.json()


def _get_events(client: httpx.Client, base_url: str, run_id: str) -> list[str]:
    response = client.get(
        f"{base_url}/api/v1/runs/{run_id}/events", headers=_headers()
    )
    response.raise_for_status()
    return [item["event_type"] for item in response.json()["events"]]


def _upload_reference(client: httpx.Client, base_url: str) -> dict[str, Any]:
    response = client.post(
        f"{base_url}/api/v1/files",
        headers=_headers(),
        files={"file": ("reference.png", _png_bytes((20, 16), (245, 190, 80)), "image/png")},
        data={"purpose": "reference_image"},
    )
    response.raise_for_status()
    return response.json()


def _download_hash(
    client: httpx.Client, base_url: str, file_id: str
) -> tuple[str, bytes]:
    response = client.get(
        f"{base_url}/api/v1/files/{file_id}/content", headers=_headers()
    )
    response.raise_for_status()
    return hashlib.sha256(response.content).hexdigest(), response.content


def _strip_request_id(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_request_id(item)
            for key, item in value.items()
            if key != "request_id"
        }
    if isinstance(value, list):
        return [_strip_request_id(item) for item in value]
    return value


def _provider_calls(call_log: Path) -> list[dict[str, Any]]:
    if not call_log.exists():
        return []
    return [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()]


def _wait_for_provider_call(
    call_log: Path,
    *,
    case_id: str,
    process: subprocess.Popen[str],
    timeout_seconds: float = 10,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("受控服务在 Provider 调用开始前退出。")
        if any(
            item.get("case_id") == case_id
            for item in _provider_calls(call_log)
        ):
            return
        time.sleep(0.05)
    raise RuntimeError("Provider 调用未在验收时限内开始。")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _scan_evidence(root: Path, *, runtime_root: Path) -> None:
    forbidden = [
        "Authorization: Bearer",
        "data:image/",
        str(REPO_ROOT),
        str(runtime_root),
        PILOT_KEY,
        "controlled-chat-key",
        "controlled-image-key",
    ]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(marker and marker in text for marker in forbidden):
            raise RuntimeError(f"证据脱敏扫描失败：{path.name}")


def _publish(staging: Path, validation: dict[str, Any]) -> bool:
    if not validation["passed"]:
        return False
    if EVIDENCE_ROOT.exists():
        raise FileExistsError(f"证据目录已存在：{EVIDENCE_ROOT.name}")
    for attempt in range(5):
        try:
            staging.replace(EVIDENCE_ROOT)
            return True
        except PermissionError:
            if EVIDENCE_ROOT.exists() or attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))
    raise RuntimeError("证据目录原子发布失败。")


def _source_sha256(path: Path) -> str:
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _implementation_versions() -> str:
    paths = {
        "app_sha256": PILOT_ROOT / "agent_service" / "app.py",
        "storage_sha256": PILOT_ROOT / "agent_service" / "storage.py",
        "file_storage_sha256": PILOT_ROOT / "agent_service" / "file_storage.py",
        "worker_sha256": PILOT_ROOT / "agent_service" / "worker.py",
        "controlled_server_sha256": Path(__file__).with_name(
            "session6_controlled_server.py"
        ),
        "verify_session6_sha256": Path(__file__),
    }
    lines = [
        f"python={sys.version.split()[0]}",
        f"fastapi={version('fastapi')}",
        f"pydantic={version('pydantic')}",
        f"pillow={version('Pillow')}",
    ]
    for name, path in paths.items():
        lines.append(f"{name}={_source_sha256(path)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if EVIDENCE_ROOT.exists():
        print(f"未执行会话6验收：证据目录已存在：{EVIDENCE_ROOT.name}", file=sys.stderr)
        return 2
    staging = Path(tempfile.mkdtemp(prefix=".pilot-session6-", dir=EVIDENCE_ROOT.parent))
    runtime_root = Path(tempfile.mkdtemp(prefix="pettrip-session6-live-"))
    call_log = runtime_root / "provider-calls.log"
    process: subprocess.Popen[str] | None = None
    published = False
    try:
        process, base_url = _start_server(
            runtime_root=runtime_root,
            provider_delay=0,
            log_path=staging / "server" / "first-server.log",
            call_log=call_log,
        )
        headers = _headers()
        with httpx.Client(timeout=20) as client:
            session_id = _create_session(client, base_url)
            input_file = _upload_reference(client, base_url)
            first_text = "第一轮：请根据参考图记录海边灯塔场景。"
            first_run_id = _create_run(
                client,
                base_url,
                session_id=session_id,
                text=first_text,
                modalities=["text", "structured_data", "image"],
                idempotency_key="session6-round-1",
                attachment={
                    "file_id": input_file["file_id"],
                    "purpose": "reference_image",
                },
            )
            first_run = _wait_for_status(
                client, base_url, first_run_id, wanted={"succeeded", "failed"}
            )
            if first_run["status"] != "succeeded":
                raise RuntimeError(f"第一轮 Run 失败：{first_run.get('error')}")
            first_attachment = first_run["output"]["attachments"][0]
            first_hash, first_bytes = _download_hash(
                client, base_url, first_attachment["file_id"]
            )
            second_text = "第二轮：请基于上一轮场景补充宠物散步建议。"
            second_run_id = _create_run(
                client,
                base_url,
                session_id=session_id,
                text=second_text,
                modalities=["text"],
                idempotency_key="session6-round-2",
            )
            second_run = _wait_for_status(
                client, base_url, second_run_id, wanted={"succeeded", "failed"}
            )
            if second_run["status"] != "succeeded":
                raise RuntimeError(f"第二轮 Run 失败：{second_run.get('error')}")
            second_messages = _get_messages(client, base_url, session_id)
            before_restart = {
                "first_run": first_run,
                "second_run": second_run,
                "messages": second_messages,
                "image_hash": first_hash,
                "image_size_bytes": len(first_bytes),
            }

        _stop_process(process)
        process = None
        process, base_url = _start_server(
            runtime_root=runtime_root,
            provider_delay=0,
            log_path=staging / "server" / "second-server.log",
            call_log=call_log,
        )
        with httpx.Client(timeout=20) as client:
            restored_first = client.get(
                f"{base_url}/api/v1/runs/{first_run_id}", headers=headers
            )
            restored_first.raise_for_status()
            restored_second = client.get(
                f"{base_url}/api/v1/runs/{second_run_id}", headers=headers
            )
            restored_second.raise_for_status()
            restored_messages = _get_messages(client, base_url, session_id)
            restored_attachment = restored_first.json()["output"]["attachments"][0]
            restored_hash, restored_bytes = _download_hash(
                client, base_url, restored_attachment["file_id"]
            )
            metadata_matches_download = (
                first_attachment["sha256"] == first_hash
                and first_attachment["size_bytes"] == len(first_bytes)
                and restored_attachment["sha256"] == restored_hash
                and restored_attachment["size_bytes"] == len(restored_bytes)
            )
            if not metadata_matches_download:
                raise RuntimeError("图片 API 元数据与下载内容不一致。")
            if restored_hash != first_hash or restored_bytes != first_bytes:
                raise RuntimeError("服务重启后图片内容或哈希发生变化。")
            after_completed_restart = {
                "first_run": restored_first.json(),
                "second_run": restored_second.json(),
                "messages": restored_messages,
                "image_hash": restored_hash,
                "image_size_bytes": len(restored_bytes),
            }

        _stop_process(process)
        process = None
        process, base_url = _start_server(
            runtime_root=runtime_root,
            provider_delay=30,
            log_path=staging / "server" / "interrupted-server.log",
            call_log=call_log,
        )
        stale_text = "执行中重启：这一轮不得自动重试。"
        with httpx.Client(timeout=20) as client:
            stale_run_id = _create_run(
                client,
                base_url,
                session_id=session_id,
                text=stale_text,
                modalities=["text"],
                idempotency_key="session6-interrupted-run",
            )
            _wait_for_status(
                client,
                base_url,
                stale_run_id,
                wanted={"running"},
                timeout_seconds=10,
            )
            _wait_for_provider_call(
                call_log,
                case_id="interrupted",
                process=process,
            )
        _kill_process(process)
        process = None
        calls_before_recovery = _provider_calls(call_log)

        process, base_url = _start_server(
            runtime_root=runtime_root,
            provider_delay=0,
            log_path=staging / "server" / "recovery-server.log",
            call_log=call_log,
        )
        with httpx.Client(timeout=20) as client:
            recovered_stale = client.get(
                f"{base_url}/api/v1/runs/{stale_run_id}", headers=headers
            )
            recovered_stale.raise_for_status()
            recovered_stale_body = recovered_stale.json()
            recovered_stale_events = _get_events(client, base_url, stale_run_id)
            resumed_text = "重启后继续：请确认会话仍可创建新 Run。"
            resumed_run_id = _create_run(
                client,
                base_url,
                session_id=session_id,
                text=resumed_text,
                modalities=["text"],
                idempotency_key="session6-after-restart",
            )
            resumed_run = _wait_for_status(
                client, base_url, resumed_run_id, wanted={"succeeded", "failed"}
            )
            resumed_messages = _get_messages(client, base_url, session_id)
        calls_after_recovery = _provider_calls(call_log)

        stale_calls_before = [
            item for item in calls_before_recovery if item.get("case_id") == "interrupted"
        ]
        stale_calls_after = [
            item for item in calls_after_recovery if item.get("case_id") == "interrupted"
        ]
        completed_messages = before_restart["messages"]["messages"]
        completed_message_sequence = [
            (item["role"], item["run_id"]) for item in completed_messages
        ]
        expected_completed_sequence = [
            ("user", first_run_id),
            ("assistant", first_run_id),
            ("user", second_run_id),
            ("assistant", second_run_id),
        ]
        same_session_two_rounds = completed_message_sequence == expected_completed_sequence
        completed_results_restored = _strip_request_id(
            before_restart
        ) == _strip_request_id(after_completed_restart)
        resumed_message_sequence = [
            (item["role"], item["run_id"])
            for item in resumed_messages["messages"]
        ]
        recovery_history_valid = resumed_message_sequence == [
            *expected_completed_sequence,
            ("user", stale_run_id),
            ("user", resumed_run_id),
            ("assistant", resumed_run_id),
        ]
        validation = {
            "session": 6,
            "passed": (
                before_restart["first_run"]["status"] == "succeeded"
                and before_restart["second_run"]["status"] == "succeeded"
                and same_session_two_rounds
                and after_completed_restart["first_run"]["status"] == "succeeded"
                and after_completed_restart["second_run"]["status"] == "succeeded"
                and completed_results_restored
                and recovered_stale_body["status"] == "failed"
                and recovered_stale_body["error"]["code"] == "SERVICE_RESTARTED"
                and recovered_stale_events
                == ["run.queued", "run.started", "run.failed"]
                and resumed_run["status"] == "succeeded"
                and recovery_history_valid
                and len(stale_calls_before) == 1
                and len(stale_calls_after) == 1
            ),
            "same_session_two_rounds": same_session_two_rounds,
            "completed_results_restored": completed_results_restored,
            "recovery_history_valid": recovery_history_valid,
            "image_hash_before_restart": before_restart["image_hash"],
            "image_hash_after_restart": after_completed_restart["image_hash"],
            "image_bytes_before_restart": before_restart["image_size_bytes"],
            "image_bytes_after_restart": after_completed_restart["image_size_bytes"],
            "image_metadata_matches_download": metadata_matches_download,
            "interrupted_run_id": stale_run_id,
            "interrupted_run_status_after_restart": recovered_stale_body["status"],
            "interrupted_run_error": recovered_stale_body.get("error"),
            "interrupted_run_events_after_restart": recovered_stale_events,
            "new_run_after_restart_id": resumed_run_id,
            "new_run_after_restart_status": resumed_run["status"],
            "provider_calls_for_interrupted_run_before_recovery": len(stale_calls_before),
            "provider_calls_for_interrupted_run_after_recovery": len(stale_calls_after),
            "provider_call_count_before_recovery": len(calls_before_recovery),
            "provider_call_count_after_recovery": len(calls_after_recovery),
        }
        _write_json(staging / "validation-report.json", validation)
        _write_json(staging / "api-tests" / "recovery-report.json", validation)
        _write_json(staging / "api-tests" / "completed-before-restart.json", before_restart)
        _write_json(
            staging / "api-tests" / "completed-after-restart.json",
            after_completed_restart,
        )
        _write_json(
            staging / "api-tests" / "interrupted-run.json",
            {
                "run_id": stale_run_id,
                "status_after_restart": recovered_stale_body["status"],
                "error": recovered_stale_body.get("error"),
                "events_after_restart": recovered_stale_events,
                "new_run_id": resumed_run_id,
                "new_run_status": resumed_run["status"],
            },
        )
        (staging / "files" / "generated-image.sha256.txt").parent.mkdir(
            parents=True, exist_ok=True
        )
        (staging / "files" / "generated-image.sha256.txt").write_text(
            f"{first_hash}  generated-image.png\n", encoding="utf-8"
        )
        (staging / "files" / "generated-image.png").write_bytes(first_bytes)
        (staging / "versions.txt").write_text(
            _implementation_versions(), encoding="utf-8"
        )
        (staging / "deployment-config.redacted.txt").write_text(
            "HOST=127.0.0.1\nPORT=<local>\nDATA_DIR=<redacted>\nDB_PATH=<redacted>\n"
            "CHAT_BASE_URL=<controlled>\nCHAT_API_KEY=<redacted>\n"
            "IMAGES_BASE_URL=<controlled>\nIMAGES_API_KEY=<redacted>\n"
            "IMAGES_MODEL=gpt-image-2\nPILOT_API_KEY=<redacted>\n",
            encoding="utf-8",
        )
        (staging / "README.md").write_text(
            "# Agent Service 会话 6 验收证据\n\n"
            "本目录使用受控 Provider 通过 HTTP API 验证 SQLite 和文件目录的跨进程恢复。"
            "同一会话完成两轮对话，服务重启后重新读取已完成 Run、消息历史和生成图片，"
            "并比较图片字节哈希。随后在 Provider 执行期间终止服务，重启后遗留 `running` "
            "Run 进入 `failed(SERVICE_RESTARTED)`，且没有自动重复调用；新的 Run 成功完成。\n\n"
            "验收客户端只访问 HTTP API，不读取 SQLite 或服务端文件目录。证据不包含 API Key、"
            "完整鉴权头、SQLite 文件、模型响应或服务端私有路径。\n",
            encoding="utf-8",
        )
        # Close the last server and persist its complete, controlled call trace.
        _stop_process(process)
        process = None
        provider_evidence = staging / "server" / "provider-calls.jsonl"
        shutil.copyfile(call_log, provider_evidence)
        provider_log_sha256 = hashlib.sha256(provider_evidence.read_bytes()).hexdigest()
        validation["provider_call_log"] = {
            "path": "server/provider-calls.jsonl",
            "sha256": provider_log_sha256,
        }
        _write_json(staging / "validation-report.json", validation)
        _write_json(staging / "api-tests" / "recovery-report.json", validation)
        _scan_evidence(staging, runtime_root=runtime_root)
        published = _publish(staging, validation)
        print(json.dumps(validation, ensure_ascii=False))
        return 0 if published else 1
    except Exception as exc:
        print(f"会话6验收失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            if process is not None:
                _stop_process(process)
        finally:
            try:
                if not published:
                    shutil.rmtree(staging, ignore_errors=True)
            finally:
                shutil.rmtree(runtime_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
