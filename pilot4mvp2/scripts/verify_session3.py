"""启动真实图片 Provider 并生成脱敏的会话 3 验收证据。"""

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
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

PILOT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PILOT_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pilot4mvp2.agent_service.config import ConfigurationError, load_settings

EVIDENCE_ROOT = PILOT_ROOT / "runs" / "pilot-multimodal-agent-session3-001"
EXPECTED_IMAGE_MODEL = "gpt-image-2"
TARGET_SIZE = (64, 48)


def _local_env() -> dict[str, str]:
    """Read local config aliases without printing or persisting secret values."""
    values: dict[str, str] = {}
    path = PILOT_ROOT / ".env.local"
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    aliases = {
        "IMAGES_BASE_URL": "IMAGE_BASE_URL",
        "IMAGES_API_KEY": "IMAGE_API_KEY",
        "IMAGES_MODEL": "IMAGE_MODEL",
    }
    for old_key, new_key in aliases.items():
        if values.get(old_key) and not values.get(new_key):
            values[new_key] = values[old_key]
    return values


def _assert_port_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex((host, port)) == 0:
            raise RuntimeError(f"端口 {host}:{port} 已被占用；未清理或覆盖任何证据。")


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 20
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
    raise RuntimeError("服务未在 20 秒内通过健康检查。")


def _poll_run(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    run_id: str,
    timeout_seconds: float = 180,
) -> tuple[list[str], dict[str, Any]]:
    statuses: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"{base_url}/api/v1/runs/{run_id}", headers=headers)
        response.raise_for_status()
        body = response.json()
        status = body["status"]
        if not statuses or statuses[-1] != status:
            statuses.append(status)
        if status in {"succeeded", "failed"}:
            return statuses, body
        time.sleep(0.5)
    raise RuntimeError("图片 Run 未在验收时限内进入终态。")


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


def _stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def _start_server(
    *,
    settings: Any,
    runtime_root: Path,
    log_path: Path,
    pilot_key: str,
    image_generation_path: str,
) -> tuple[subprocess.Popen[str], str]:
    env = dict(os.environ)
    env.update(_local_env())
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "PILOT_API_KEY": pilot_key,
            "DATA_DIR": str(runtime_root),
            "DB_PATH": str(runtime_root / "agent.db"),
            "IMAGE_MODEL": EXPECTED_IMAGE_MODEL,
            "IMAGE_GENERATION_PATH": image_generation_path,
            "IMAGE_CANVAS_WIDTH": str(TARGET_SIZE[0]),
            "IMAGE_CANVAS_HEIGHT": str(TARGET_SIZE[1]),
            "IMAGE_MAX_DECODED_BYTES": str(20 * 1024 * 1024),
            "IMAGE_MAX_PIXELS": str(20_000_000),
            "IMAGE_TIMEOUT": "120",
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
    return process, f"http://{settings.host}:{settings.port}"


def _redact_log(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    text = text.replace(str(REPO_ROOT), "<redacted-root>")
    for value in _local_env().values():
        if value and len(value) > 12:
            text = text.replace(value, "<redacted-secret>")
    path.write_text(text, encoding="utf-8")


def _scan_evidence(root: Path, secrets_to_reject: list[str]) -> None:
    forbidden_markers = [
        "Authorization: Bearer",
        "data:image/",
        str(REPO_ROOT),
    ]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in [*forbidden_markers, *secrets_to_reject]:
            if marker and marker in text:
                raise RuntimeError(f"证据脱敏扫描失败：{path.name}")


def _db_counts(db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(db_path)
    try:
        return {
            "generated_files": connection.execute(
                "SELECT COUNT(*) FROM files WHERE source = 'agent_generated'"
            ).fetchone()[0],
            "output_relations": connection.execute(
                "SELECT COUNT(*) FROM message_files WHERE role = 'output'"
            ).fetchone()[0],
            "assistant_messages": connection.execute(
                "SELECT COUNT(*) FROM messages WHERE role = 'assistant'"
            ).fetchone()[0],
        }
    finally:
        connection.close()


def _run_image_case(
    *,
    settings: Any,
    staging_root: Path,
    runtime_root: Path,
    image_generation_path: str,
    expected_status: str,
) -> dict[str, Any]:
    pilot_key = secrets.token_urlsafe(32)
    log_path = staging_root / "server" / f"{expected_status}.log"
    process, base_url = _start_server(
        settings=settings,
        runtime_root=runtime_root,
        log_path=log_path,
        pilot_key=pilot_key,
        image_generation_path=image_generation_path,
    )
    try:
        health = _wait_for_health(base_url, process)
        headers = {"Authorization": f"Bearer {pilot_key}"}
        with httpx.Client(timeout=15) as client:
            session = client.post(f"{base_url}/api/v1/sessions", headers=headers)
            session.raise_for_status()
            created = client.post(
                f"{base_url}/api/v1/runs",
                headers={**headers, "Idempotency-Key": secrets.token_hex(12)},
                json={
                    "session_id": session.json()["session_id"],
                    "input": {"text": "生成一张真实 Provider 图片验收图"},
                    "response_format": {"modalities": ["image"]},
                },
            )
            created.raise_for_status()
            statuses, terminal = _poll_run(
                client, base_url, headers, created.json()["run_id"]
            )
            result: dict[str, Any] = {
                "health_status": health["status"],
                "create_status": created.status_code,
                "statuses_observed": statuses,
                "terminal_status": terminal["status"],
                "error": terminal.get("error"),
            }
            if terminal["status"] == "succeeded":
                attachment = terminal["output"]["attachments"][0]
                first = client.get(
                    f"{base_url}{attachment['download_url']}", headers=headers
                )
                second = client.get(
                    f"{base_url}{attachment['download_url']}", headers=headers
                )
                first.raise_for_status()
                second.raise_for_status()
                with Image.open(io.BytesIO(first.content)) as image:
                    image_format = image.format
                    image_size = image.size
                if expected_status == "positive":
                    copy_path = staging_root / "files" / "generated-image.png"
                    copy_path.parent.mkdir(parents=True, exist_ok=True)
                    copy_path.write_bytes(first.content)
                saved_copy_hash = (
                    hashlib.sha256(copy_path.read_bytes()).hexdigest()
                    if expected_status == "positive"
                    else None
                )
                result.update(
                    {
                        "file_id": attachment["file_id"],
                        "mime_type": attachment["mime_type"],
                        "width": attachment["width"],
                        "height": attachment["height"],
                        "size_bytes": attachment["size_bytes"],
                        "sha256": attachment["sha256"],
                        "download_status": first.status_code,
                        "repeat_download_status": second.status_code,
                        "download_hash_matches": hashlib.sha256(
                            first.content
                        ).hexdigest()
                        == attachment["sha256"],
                        "repeat_download_hash_matches": first.content == second.content,
                        "saved_copy": "files/generated-image.png"
                        if expected_status == "positive"
                        else None,
                        "saved_copy_hash_matches": saved_copy_hash
                        == attachment["sha256"]
                        if saved_copy_hash is not None
                        else None,
                        "pillow_format": image_format,
                        "pillow_size": list(image_size),
                    }
                )
            events = client.get(
                f"{base_url}/api/v1/runs/{created.json()['run_id']}/events",
                headers=headers,
            )
            events.raise_for_status()
            result["events"] = [
                item["event_type"] for item in events.json()["events"]
            ]
            return result
    finally:
        _stop(process)
        _redact_log(log_path)


def main() -> int:
    local_env = _local_env()
    if not local_env.get("IMAGE_API_KEY") or not local_env.get("IMAGE_BASE_URL"):
        print("未执行真实验收：缺少 IMAGE_BASE_URL 或 IMAGE_API_KEY。", file=sys.stderr)
        return 2
    try:
        settings = load_settings(
            overrides={
                **local_env,
                "IMAGE_MODEL": EXPECTED_IMAGE_MODEL,
                "PILOT_API_KEY": secrets.token_urlsafe(32),
            }
        )
        if settings.image_model != EXPECTED_IMAGE_MODEL:
            raise ConfigurationError(
                "真实验收的 IMAGE_MODEL 未解析为 gpt-image-2。"
            )
    except ConfigurationError as exc:
        print(f"未执行真实验收：{exc}", file=sys.stderr)
        return 2
    if EVIDENCE_ROOT.exists():
        print(f"未执行真实验收：证据目录已存在：{EVIDENCE_ROOT.name}", file=sys.stderr)
        return 2
    _assert_port_available(settings.host, settings.port)

    staging_root = Path(
        tempfile.mkdtemp(prefix=".pilot-session3-", dir=EVIDENCE_ROOT.parent)
    )
    runtime_root = Path(tempfile.mkdtemp(prefix="pettrip-session3-runtime-"))
    bad_runtime_root = Path(tempfile.mkdtemp(prefix="pettrip-session3-bad-runtime-"))
    published = False
    try:
        positive = _run_image_case(
            settings=settings,
            staging_root=staging_root,
            runtime_root=runtime_root,
            image_generation_path="/images/generations",
            expected_status="positive",
        )
        negative = _run_image_case(
            settings=settings,
            staging_root=staging_root,
            runtime_root=bad_runtime_root,
            image_generation_path="/wrong-image-endpoint",
            expected_status="negative",
        )
        negative_counts = _db_counts(bad_runtime_root / "agent.db")
        negative_generated = list(
            (bad_runtime_root / "files" / "generated").glob("*")
        )
        negative.update(
            {
                "sqlite_counts_after_failure": negative_counts,
                "generated_files_after_failure": len(negative_generated),
            }
        )
        validation = {
            "session": 3,
            "passed": (
                settings.image_model == EXPECTED_IMAGE_MODEL
                and positive["health_status"] == "ok"
                and positive["create_status"] == 202
                and positive["terminal_status"] == "succeeded"
                and _has_legal_status_sequence(positive["statuses_observed"])
                and positive["mime_type"] == "image/png"
                and [positive["width"], positive["height"]] == list(TARGET_SIZE)
                and positive["pillow_format"] == "PNG"
                and positive["pillow_size"] == list(TARGET_SIZE)
                and positive["download_hash_matches"]
                and positive["repeat_download_hash_matches"]
                and positive["saved_copy_hash_matches"]
                and positive["events"] == [
                    "run.queued",
                    "run.started",
                    "image_generation.started",
                    "artifact.created",
                    "message.created",
                    "run.completed",
                ]
                and negative["terminal_status"] == "failed"
                and negative["error"]["code"] == "IMAGE_PROVIDER_UNAVAILABLE"
                and negative["sqlite_counts_after_failure"]
                == {
                    "generated_files": 0,
                    "output_relations": 0,
                    "assistant_messages": 0,
                }
                and negative["generated_files_after_failure"] == 0
            ),
            "provider_protocol": "openai-compatible-images-generations-b64-json",
            "expected_model": EXPECTED_IMAGE_MODEL,
            "configured_model": settings.image_model,
            "model_assertion_passed": settings.image_model == EXPECTED_IMAGE_MODEL,
            "target_canvas": list(TARGET_SIZE),
            "negative_case": "wrong_generation_endpoint",
        }
        _write_json(staging_root / "api-tests" / "image-output-run.json", positive)
        _write_json(staging_root / "api-tests" / "provider-negative.json", negative)
        _write_json(staging_root / "validation-report.json", validation)
        (staging_root / "files").mkdir(parents=True, exist_ok=True)
        (staging_root / "files" / "generated-image.sha256.txt").write_text(
            f"{positive.get('sha256', '<none>')}  generated-image.png\n",
            encoding="utf-8",
        )
        _write_json(
            staging_root / "files" / "generated-image-metadata.json",
            {
                key: positive.get(key)
                for key in (
                    "file_id",
                    "mime_type",
                    "size_bytes",
                    "sha256",
                    "width",
                    "height",
                    "download_hash_matches",
                    "repeat_download_hash_matches",
                    "saved_copy",
                    "saved_copy_hash_matches",
                )
            },
        )
        (staging_root / "deployment-config.redacted.txt").write_text(
            "HOST=<local>\nPORT=<local>\nIMAGE_BASE_URL=<redacted>\n"
            f"IMAGE_API_KEY=<redacted>\nIMAGE_MODEL={settings.image_model}\n"
            "IMAGE_GENERATION_PATH=/images/generations\n"
            "IMAGE_CANVAS_WIDTH=64\nIMAGE_CANVAS_HEIGHT=48\n"
            "PILOT_API_KEY=<ephemeral>\n",
            encoding="utf-8",
        )
        (staging_root / "versions.txt").write_text(
            f"python={sys.version.split()[0]}\nservice={settings.service_version}\n",
            encoding="utf-8",
        )
        (staging_root / "README.md").write_text(
            "# Agent Service 会话 3 验收证据\n\n"
            "本目录记录强制使用 gpt-image-2 的真实图片 Provider 调用、目标画布规范化、"
            "鉴权下载、重复下载哈希一致和错误端点负例。`files/generated-image.png` 是"
            "客户端通过鉴权下载的规范化 PNG 副本，供人工检查。Provider 原始 Base64、"
            "API Key、完整鉴权头、SQLite 文件、Provider 原始响应和服务端路径均未写入"
            "证据。\n",
            encoding="utf-8",
        )
        _scan_evidence(
            staging_root,
            [
                local_env.get("IMAGE_API_KEY", ""),
                local_env.get("CHAT_API_KEY", ""),
                settings.pilot_api_key,
            ],
        )
        staging_root.replace(EVIDENCE_ROOT)
        published = True
        print(json.dumps(validation, ensure_ascii=False))
        return 0 if validation["passed"] else 1
    finally:
        if not published:
            shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(runtime_root, ignore_errors=True)
        shutil.rmtree(bad_runtime_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
