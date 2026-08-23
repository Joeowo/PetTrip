"""执行会话 4 黑盒验收并生成脱敏证据。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from importlib.metadata import version
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import httpx
from pydantic import ValidationError

PILOT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PILOT_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_service.config import ConfigurationError, load_settings
from agent_service.api_test_client.structured_dto import read_scene_draft_v01

EVIDENCE_ROOT = PILOT_ROOT / "runs" / "pilot-multimodal-agent-session4-001"
SCHEMA_NAME = "scene_draft"
SCHEMA_VERSION = "0.1"


class _FixtureProvider:
    def __init__(self) -> None:
        self.call_count = 0
        self._server: ThreadingHTTPServer | None = None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                fixture.call_count += 1
                prompt = request["messages"][-1]["content"]
                if prompt == "NEGATIVE_MISSING_TITLE":
                    payload = {
                        "type": "scene_draft",
                        "schema_version": "0.1",
                        "theme": "seaside",
                        "summary": "缺少标题的受控负例。",
                        "landmark_kind": "lighthouse",
                    }
                elif prompt == "NEGATIVE_WRONG_TYPE":
                    payload = {
                        "type": "scene_plan",
                        "schema_version": "0.1",
                        "title": "错误类型",
                        "theme": "seaside",
                        "summary": "类型错误的受控负例。",
                        "landmark_kind": "lighthouse",
                    }
                else:
                    self.send_error(400)
                    return
                body = json.dumps(
                    {"choices": [{"message": {"content": json.dumps(payload)}}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._handler_type = Handler

    @contextmanager
    def running(self) -> Iterator[str]:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type)
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = self._server.server_address
            yield f"http://{host}:{port}/v1"
        finally:
            self._server.shutdown()
            self._server.server_close()
            thread.join(timeout=5)
            self._server = None


def _load_local_env() -> None:
    path_value = os.environ.get("PETTRIP_LOCAL_ENV_PATH", "").strip()
    if not path_value:
        return
    path = Path(path_value)
    if not path.is_file():
        raise ConfigurationError("PETTRIP_LOCAL_ENV_PATH 指向的文件不存在。")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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


def _start_server(
    *,
    settings: Any,
    runtime_root: Path,
    log_path: Path,
    pilot_key: str,
    chat_base_url: str,
    chat_api_key: str,
    chat_model: str,
) -> tuple[subprocess.Popen[str], str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "PILOT_API_KEY": pilot_key,
            "DATA_DIR": str(runtime_root),
            "DB_PATH": str(runtime_root / "agent.db"),
            "CHAT_BASE_URL": chat_base_url,
            "CHAT_API_KEY": chat_api_key,
            "CHAT_MODEL": chat_model,
            "CHAT_TEMPERATURE": "0",
            "CHAT_MAX_TOKENS": "512",
        }
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "agent_service.run_server"],
            cwd=REPO_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        log_file.close()
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


def _poll_run(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    run_id: str,
    *,
    timeout_seconds: float = 120,
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
        time.sleep(0.2)
    raise RuntimeError("结构化 Run 未在验收时限内进入终态。")


def _create_and_poll(
    *,
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    session_id: str,
    text: str,
    schema_version: str,
) -> tuple[str, list[str], dict[str, Any]]:
    response = client.post(
        f"{base_url}/api/v1/runs",
        headers={**headers, "Idempotency-Key": secrets.token_hex(12)},
        json={
            "session_id": session_id,
            "input": {"text": text},
            "response_format": {
                "modalities": ["structured_data"],
                "structured_output": {
                    "schema_name": SCHEMA_NAME,
                    "schema_version": schema_version,
                },
            },
        },
    )
    response.raise_for_status()
    run_id = response.json()["run_id"]
    statuses, terminal = _poll_run(client, base_url, headers, run_id)
    return run_id, statuses, terminal


def _session(client: httpx.Client, base_url: str, headers: dict[str, str]) -> str:
    response = client.post(f"{base_url}/api/v1/sessions", headers=headers)
    response.raise_for_status()
    return response.json()["session_id"]


def _event_types(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    run_id: str,
) -> list[str]:
    response = client.get(f"{base_url}/api/v1/runs/{run_id}/events", headers=headers)
    response.raise_for_status()
    return [item["event_type"] for item in response.json()["events"]]


def _verify_persistence(db_path: Path, run_id: str, expected: dict[str, Any]) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT r.output_structured, m.structured_data "
            "FROM runs r JOIN messages m ON m.run_id = r.id AND m.role = 'assistant' "
            "WHERE r.id = ?",
            (run_id,),
        ).fetchone()
    finally:
        connection.close()
    return bool(
        row
        and json.loads(row[0]) == expected
        and json.loads(row[1]) == expected
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _redacted_logs(
    staging_root: Path,
    raw_logs: list[Path],
    forbidden_values: list[str],
    runtime_roots: list[Path],
) -> None:
    chunks: list[str] = []
    replacements = [str(REPO_ROOT), *(str(path) for path in runtime_roots)]
    replacements.extend(value for value in forbidden_values if value)
    for raw_log in raw_logs:
        text = raw_log.read_text(encoding="utf-8", errors="replace")
        for value in replacements:
            text = text.replace(value, "<redacted>")
        chunks.append(text)
        raw_log.unlink()
    destination = staging_root / "server" / "redacted.log"
    destination.write_text("\n".join(chunks), encoding="utf-8")


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


def _legal_status_sequence(statuses: list[str], terminal: str) -> bool:
    order = {"queued": 0, "running": 1, "succeeded": 2, "failed": 2}
    return bool(statuses) and statuses[-1] == terminal and all(
        current in order
        and (index == 0 or order[current] >= order[statuses[index - 1]])
        for index, current in enumerate(statuses)
    )


def _sha256(path: Path) -> str:
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _implementation_versions(settings: Any) -> str:
    paths = {
        "app_sha256": PILOT_ROOT / "agent_service" / "app.py",
        "chat_provider_sha256": PILOT_ROOT / "agent_service" / "chat_provider.py",
        "schemas_sha256": PILOT_ROOT / "agent_service" / "schemas.py",
        "storage_sha256": PILOT_ROOT / "agent_service" / "storage.py",
        "structured_output_sha256": PILOT_ROOT
        / "agent_service"
        / "structured_output.py",
        "worker_sha256": PILOT_ROOT / "agent_service" / "worker.py",
        "structured_dto_sha256": PILOT_ROOT
        / "api_test_client"
        / "structured_dto.py",
        "verify_session4_sha256": Path(__file__).resolve(),
    }
    lines = [
        f"python={sys.version.split()[0]}",
        f"service={settings.service_version}",
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
    try:
        _load_local_env()
    except ConfigurationError as exc:
        print(f"未执行真实验收：{exc}", file=sys.stderr)
        return 2
    try:
        pilot_key = secrets.token_urlsafe(32)
        settings = load_settings(overrides={"PILOT_API_KEY": pilot_key})
    except ConfigurationError as exc:
        print(f"未执行真实验收：{exc}", file=sys.stderr)
        return 2
    if EVIDENCE_ROOT.exists():
        print(f"未执行真实验收：证据目录已存在：{EVIDENCE_ROOT.name}", file=sys.stderr)
        return 2
    _assert_port_available(settings.host, settings.port)

    staging_root = Path(
        tempfile.mkdtemp(prefix=".pilot-session4-", dir=EVIDENCE_ROOT.parent)
    )
    live_runtime = Path(tempfile.mkdtemp(prefix="pettrip-session4-live-"))
    fixture_runtime = Path(tempfile.mkdtemp(prefix="pettrip-session4-fixture-"))
    raw_logs = [
        staging_root / "server" / "live.raw.log",
        staging_root / "server" / "fixture.raw.log",
    ]
    published = False
    try:
        live_process, base_url = _start_server(
            settings=settings,
            runtime_root=live_runtime,
            log_path=raw_logs[0],
            pilot_key=pilot_key,
            chat_base_url=settings.chat_base_url,
            chat_api_key=settings.chat_api_key,
            chat_model=settings.chat_model,
        )
        try:
            headers = {"Authorization": f"Bearer {pilot_key}"}
            with httpx.Client(timeout=15) as client:
                session_id = _session(client, base_url, headers)
                run_id, statuses, terminal = _create_and_poll(
                    client=client,
                    base_url=base_url,
                    headers=headers,
                    session_id=session_id,
                    text=(
                        "返回一个 PetTrip 海边旅行场景草案。所有字符串字段必须非空，"
                        "并严格遵循请求的 JSON Schema。"
                    ),
                    schema_version=SCHEMA_VERSION,
                )
                if terminal["status"] != "succeeded":
                    raise RuntimeError(
                        "真实 Provider 结构化正例失败："
                        f"{terminal.get('error', {}).get('code', 'unknown')}"
                    )
                dto = read_scene_draft_v01(terminal)
                structured_data = dto.model_dump(mode="json")
                live_events = _event_types(client, base_url, headers, run_id)
                live_result = {
                    "provider": "real_chat_provider",
                    "provider_configuration_sha256": hashlib.sha256(
                        f"{settings.chat_base_url}\n{settings.chat_model}".encode("utf-8")
                    ).hexdigest(),
                    "schema_name": SCHEMA_NAME,
                    "schema_version": SCHEMA_VERSION,
                    "statuses_observed": statuses,
                    "terminal_status": terminal["status"],
                    "events": live_events,
                    "fixed_dto_valid": True,
                    "structured_data": structured_data,
                    "text_field_present": "text" in terminal["output"],
                }
        finally:
            _stop(live_process)
        live_result["sqlite_and_message_match_api"] = _verify_persistence(
            live_runtime / "agent.db", run_id, structured_data
        )

        fixture = _FixtureProvider()
        with fixture.running() as fixture_url:
            fixture_key = "fixture-provider-key"
            fixture_process, fixture_base_url = _start_server(
                settings=settings,
                runtime_root=fixture_runtime,
                log_path=raw_logs[1],
                pilot_key=pilot_key,
                chat_base_url=fixture_url,
                chat_api_key=fixture_key,
                chat_model="fixture-structured-model",
            )
            try:
                headers = {"Authorization": f"Bearer {pilot_key}"}
                with httpx.Client(timeout=15) as client:
                    session_id = _session(client, fixture_base_url, headers)
                    negative_results: list[dict[str, Any]] = []
                    for case_name, text, version in (
                        ("missing_title", "NEGATIVE_MISSING_TITLE", "0.1"),
                        ("wrong_type", "NEGATIVE_WRONG_TYPE", "0.1"),
                        ("unsupported_version", "MUST_NOT_REACH_PROVIDER", "9.9"),
                    ):
                        negative_run_id, case_statuses, case_terminal = _create_and_poll(
                            client=client,
                            base_url=fixture_base_url,
                            headers=headers,
                            session_id=session_id,
                            text=text,
                            schema_version=version,
                        )
                        case_events = _event_types(
                            client,
                            fixture_base_url,
                            headers,
                            negative_run_id,
                        )
                        connection = sqlite3.connect(fixture_runtime / "agent.db")
                        try:
                            assistant_count = connection.execute(
                                "SELECT COUNT(*) FROM messages "
                                "WHERE run_id = ? AND role = 'assistant'",
                                (negative_run_id,),
                            ).fetchone()[0]
                        finally:
                            connection.close()
                        negative_results.append(
                            {
                                "case": case_name,
                                "schema_version": version,
                                "statuses_observed": case_statuses,
                                "terminal_status": case_terminal["status"],
                                "events": case_events,
                                "error": case_terminal.get("error"),
                                "output_present": "output" in case_terminal,
                                "assistant_messages_persisted": assistant_count,
                            }
                        )
            finally:
                _stop(fixture_process)
        fixture_calls = fixture.call_count

        text_fallback_rejected = False
        try:
            read_scene_draft_v01(
                {
                    "status": "succeeded",
                    "output": {"text": json.dumps(structured_data, ensure_ascii=False)},
                }
            )
        except (ValueError, ValidationError):
            text_fallback_rejected = True

        negative_passed = all(
            item["terminal_status"] == "failed"
            and item["error"]
            == {
                "code": "STRUCTURED_OUTPUT_INVALID",
                "message": "结构化输出不符合请求的 Schema。",
                "retryable": False,
            }
            and not item["output_present"]
            and item["assistant_messages_persisted"] == 0
            and item["events"] == ["run.queued", "run.started", "run.failed"]
            for item in negative_results
        )
        validation = {
            "session": 4,
            "passed": (
                live_result["terminal_status"] == "succeeded"
                and live_result["fixed_dto_valid"]
                and live_result["sqlite_and_message_match_api"]
                and not live_result["text_field_present"]
                and live_result["events"]
                == [
                    "run.queued",
                    "run.started",
                    "message.created",
                    "run.completed",
                ]
                and negative_passed
                and fixture_calls == 2
                and text_fallback_rejected
            ),
            "real_provider_positive": live_result["terminal_status"] == "succeeded",
            "provider_configuration_sha256": live_result[
                "provider_configuration_sha256"
            ],
            "controlled_provider_negative_cases": [
                "missing_title",
                "wrong_type",
            ],
            "unsupported_version_failed_before_provider": fixture_calls == 2,
            "client_uses_fixed_dto": True,
            "client_text_json_fallback_rejected": text_fallback_rejected,
        }
        _write_json(staging_root / "api-tests" / "structured-run.json", live_result)
        _write_json(
            staging_root / "api-tests" / "negative-cases.json",
            {
                "provider_fixture_calls": fixture_calls,
                "cases": negative_results,
                "client_text_json_fallback_rejected": text_fallback_rejected,
            },
        )
        _write_json(staging_root / "validation-report.json", validation)
        (staging_root / "deployment-config.redacted.txt").write_text(
            "HOST=<local>\nPORT=<local>\nCHAT_BASE_URL=<redacted>\n"
            "CHAT_API_KEY=<redacted>\nCHAT_MODEL=<redacted>\n"
            "PILOT_API_KEY=<ephemeral>\n",
            encoding="utf-8",
        )
        (staging_root / "versions.txt").write_text(
            _implementation_versions(settings), encoding="utf-8"
        )
        (staging_root / "README.md").write_text(
            "# Agent Service 会话 4 验收证据\n\n"
            "本目录记录真实 Chat Provider 的 `scene_draft` `0.1` 正例。当前网关使用\n"
            "`response_format=json_object`，服务端随后执行独立的版本注册表查找、"
            "JSON Schema\n校验和固定 DTO 校验。\n\n"
            "缺少 `title` 和错误 `type` 负例通过本地 OpenAI-compatible 受控 "
            "Provider 注入。\n不支持版本由同一正式服务链路验证，并确认在 Provider 调用"
            "前失败。API 测试客户端只读取\n`output.structured_data` 并使用固定 DTO，"
            "不从文本提取 JSON。\n\n"
            "证据不包含 API Key、完整鉴权头、Provider 原始响应、SQLite 文件或服务端"
            "路径。\n",
            encoding="utf-8",
        )
        _redacted_logs(
            staging_root,
            raw_logs,
            [settings.chat_api_key, pilot_key, fixture_key],
            [live_runtime, fixture_runtime],
        )
        _scan_evidence(staging_root, [settings.chat_api_key, pilot_key, fixture_key])
        published = _publish_evidence(staging_root, validation)
        print(json.dumps(validation, ensure_ascii=False))
        return 0 if published else 1
    finally:
        if not published:
            shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(live_runtime, ignore_errors=True)
        shutil.rmtree(fixture_runtime, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
