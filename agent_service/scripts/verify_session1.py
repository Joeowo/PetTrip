"""启动真实服务并原子生成会话 1 的脱敏验收证据。"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

PILOT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PILOT_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_service.config import ConfigurationError, load_settings

EVIDENCE_ROOT = PILOT_ROOT / "runs" / "pilot-multimodal-agent-001"


def _assert_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex((host, port)) == 0:
            raise RuntimeError(f"端口 {host}:{port} 已被占用；未清理或覆盖任何证据。")


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务在健康检查前退出。")
        try:
            response = httpx.get(f"{base_url}/health", timeout=1)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("服务未在 15 秒内通过健康检查。")


def _poll_run(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    run_id: str,
) -> tuple[list[str], dict[str, Any]]:
    statuses: list[str] = []
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"{base_url}/api/v1/runs/{run_id}", headers=headers)
        response.raise_for_status()
        body = response.json()
        status = body["status"]
        if not statuses or statuses[-1] != status:
            statuses.append(status)
        if status in {"succeeded", "failed"}:
            return statuses, body
        time.sleep(0.2)
    raise RuntimeError("Run 未在 90 秒内进入终态。")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _has_legal_status_sequence(statuses: list[str]) -> bool:
    order = {"queued": 0, "running": 1, "succeeded": 2, "failed": 2}
    return bool(statuses) and all(
        status in order
        and (index == 0 or order[status] >= order[statuses[index - 1]])
        for index, status in enumerate(statuses)
    )


def _scan_evidence(root: Path, secrets_to_reject: list[str]) -> None:
    forbidden_markers = ["Authorization: Bearer", str(REPO_ROOT)]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in [*forbidden_markers, *secrets_to_reject]:
            if marker and marker in text:
                raise RuntimeError(f"证据脱敏扫描失败：{path.name}")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"未执行真实验收：{exc}", file=sys.stderr)
        return 2
    if EVIDENCE_ROOT.exists():
        print(
            f"未执行真实验收：正式证据目录已存在：{EVIDENCE_ROOT.name}",
            file=sys.stderr,
        )
        return 2

    _assert_port_available(settings.host, settings.port)
    base_url = f"http://{settings.host}:{settings.port}"
    EVIDENCE_ROOT.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=".pilot-multimodal-agent-001-", dir=EVIDENCE_ROOT.parent
        )
    )
    published = False

    try:
        server_log = staging_root / "server" / "redacted.log"
        server_log.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        with server_log.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                [sys.executable, "-m", "agent_service.run_server"],
                cwd=REPO_ROOT,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                health = _wait_for_health(base_url, process)
                with httpx.Client(timeout=10) as client:
                    missing_auth = client.post(f"{base_url}/api/v1/sessions")
                    wrong_auth = client.post(
                        f"{base_url}/api/v1/sessions",
                        headers={"Authorization": "Bearer deliberately-wrong-key"},
                    )
                    good_headers = {
                        "Authorization": f"Bearer {settings.pilot_api_key}"
                    }
                    session_response = client.post(
                        f"{base_url}/api/v1/sessions", headers=good_headers
                    )
                    session_response.raise_for_status()
                    session_id = session_response.json()["session_id"]
                    run_response = client.post(
                        f"{base_url}/api/v1/runs",
                        headers={
                            **good_headers,
                            "Idempotency-Key": f"verify-{secrets.token_hex(8)}",
                        },
                        json={
                            "session_id": session_id,
                            "input": {"text": "请用一句简短中文确认你能回复文本。"},
                            "response_format": {"modalities": ["text"]},
                        },
                    )
                    run_response.raise_for_status()
                    run_id = run_response.json()["run_id"]
                    statuses, terminal = _poll_run(
                        client, base_url, good_headers, run_id
                    )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        log_has_request_id = "request_id=req_" in server_log.read_text(
            encoding="utf-8", errors="replace"
        )
        authentication = {
            "health_status": health["status"],
            "missing_key_status": missing_auth.status_code,
            "missing_key_error": missing_auth.json()["error"]["code"],
            "wrong_key_status": wrong_auth.status_code,
            "wrong_key_error": wrong_auth.json()["error"]["code"],
            "correct_key_status": session_response.status_code,
        }
        text_run = {
            "create_status": run_response.status_code,
            "run_id": run_id,
            "statuses_observed": statuses,
            "terminal_status": terminal["status"],
            "assistant_text": terminal.get("output", {}).get("text"),
            "error": terminal.get("error"),
        }
        validation = {
            "session": 1,
            "passed": (
                missing_auth.status_code == 401
                and wrong_auth.status_code == 401
                and session_response.status_code == 201
                and terminal["status"] == "succeeded"
                and bool(text_run["assistant_text"])
                and _has_legal_status_sequence(statuses)
                and log_has_request_id
            ),
            "provider_protocol": "openai-compatible-chat-completions",
            "model_configured": True,
            "log_has_request_id": log_has_request_id,
        }
        _write_json(staging_root / "api-tests" / "authentication.json", authentication)
        _write_json(staging_root / "api-tests" / "text-run.json", text_run)
        _write_json(staging_root / "validation-report.json", validation)
        (staging_root / "deployment-config.redacted.txt").write_text(
            "HOST=<local>\nPORT=<local>\nCHAT_BASE_URL=<redacted>\n"
            "CHAT_API_KEY=<redacted>\nCHAT_MODEL=<configured>\n"
            "PILOT_API_KEY=<redacted>\n",
            encoding="utf-8",
        )
        (staging_root / "versions.txt").write_text(
            f"python={sys.version.split()[0]}\nservice={settings.service_version}\n",
            encoding="utf-8",
        )
        (staging_root / "README.md").write_text(
            "# Agent Service 会话 1 验收证据\n\n"
            "本目录记录 Bearer 鉴权、纯文本异步 Run 和真实 OpenAI 兼容 Chat "
            "Provider 调用的脱敏结果。证据不包含密钥、完整 Authorization Header、"
            "SQLite 文件、Provider 原始响应或服务器私有路径。\n",
            encoding="utf-8",
        )
        _scan_evidence(
            staging_root, [settings.pilot_api_key, settings.chat_api_key]
        )
        staging_root.replace(EVIDENCE_ROOT)
        published = True
        print(json.dumps(validation, ensure_ascii=False))
        return 0 if validation["passed"] else 1
    finally:
        if not published:
            shutil.rmtree(staging_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
