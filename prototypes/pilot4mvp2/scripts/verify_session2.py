"""启动真实服务并原子生成会话 2 的脱敏验收证据。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
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

EVIDENCE_ROOT = PILOT_ROOT / "runs" / "pilot-multimodal-agent-session2-001"
QUESTION = (
    "图片左半边和右半边分别是什么颜色？"
    "只输出 JSON 对象，键必须是 left 和 right，值使用英文颜色名；不要输出其他内容。"
)


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
    deadline = time.monotonic() + 120
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
    raise RuntimeError("Run 未在 120 秒内进入终态。")


def _image_bytes(image_format: str) -> bytes:
    image = Image.new("RGB", (128, 64), color=(255, 0, 0))
    for x in range(64, 128):
        for y in range(64):
            image.putpixel((x, y), (0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


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


def _sqlite_has_no_binary(db_path: Path) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            if any(str(row[2]).upper() == "BLOB" for row in columns):
                return False
            column_names = [str(row[1]).replace('"', '""') for row in columns]
            for column in column_names:
                rows = connection.execute(
                    f'SELECT "{column}" FROM "{table}"'
                ).fetchall()
                for (value,) in rows:
                    if isinstance(value, bytes):
                        return False
                    if isinstance(value, str) and re.search(
                        r"data:[^;]+;base64,",
                        value,
                        flags=re.IGNORECASE,
                    ):
                        return False
        return True
    finally:
        connection.close()


def _redact_server_log(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(
        r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)",
        "<redacted-traceback>",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|/)[^\s\"']+",
        "<redacted-path>",
        text,
    )
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


def main() -> int:
    verification_key = secrets.token_urlsafe(32)
    try:
        settings = load_settings(overrides={"PILOT_API_KEY": verification_key})
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
        tempfile.mkdtemp(prefix=".pilot-session2-", dir=EVIDENCE_ROOT.parent)
    )
    runtime_root = Path(tempfile.mkdtemp(prefix="pettrip-session2-runtime-"))
    published = False
    png = _image_bytes("PNG")
    jpeg = _image_bytes("JPEG")

    try:
        server_log = staging_root / "server" / "redacted.log"
        server_log.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "PYTHONPATH": str(REPO_ROOT),
                "PILOT_API_KEY": verification_key,
                "DATA_DIR": str(runtime_root),
                "DB_PATH": str(runtime_root / "agent.db"),
                "MAX_UPLOAD_BYTES": str(10 * 1024 * 1024),
                "MAX_IMAGE_DIMENSION": "4096",
                "MAX_IMAGE_PIXELS": "20000000",
            }
        )
        with server_log.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                [sys.executable, "-m", "pilot4mvp2.agent_service.run_server"],
                cwd=REPO_ROOT,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                health = _wait_for_health(base_url, process)
                good_headers = {"Authorization": f"Bearer {verification_key}"}
                with httpx.Client(timeout=15) as client:
                    upload = client.post(
                        f"{base_url}/api/v1/files",
                        headers=good_headers,
                        data={"purpose": "vision_input"},
                        files={"file": ("left-red-right-blue.png", png, "image/png")},
                    )
                    upload.raise_for_status()
                    metadata = upload.json()
                    file_id = metadata["file_id"]

                    downloaded = client.get(
                        f"{base_url}/api/v1/files/{file_id}/content",
                        headers=good_headers,
                    )
                    downloaded.raise_for_status()

                    disguised = client.post(
                        f"{base_url}/api/v1/files",
                        headers=good_headers,
                        data={"purpose": "vision_input"},
                        files={"file": ("disguised.png", jpeg, "image/png")},
                    )
                    oversized = client.post(
                        f"{base_url}/api/v1/files",
                        headers=good_headers,
                        data={"purpose": "vision_input"},
                        files={
                            "file": (
                                "oversized.png",
                                b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024),
                                "image/png",
                            )
                        },
                    )

                    session = client.post(
                        f"{base_url}/api/v1/sessions", headers=good_headers
                    )
                    session.raise_for_status()
                    run = client.post(
                        f"{base_url}/api/v1/runs",
                        headers={
                            **good_headers,
                            "Idempotency-Key": f"vision-{secrets.token_hex(8)}",
                        },
                        json={
                            "session_id": session.json()["session_id"],
                            "input": {
                                "text": QUESTION,
                                "attachments": [
                                    {"file_id": file_id, "purpose": "vision_input"}
                                ],
                            },
                            "response_format": {"modalities": ["text"]},
                        },
                    )
                    run.raise_for_status()
                    statuses, terminal = _poll_run(
                        client, base_url, good_headers, run.json()["run_id"]
                    )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        assistant_text = terminal.get("output", {}).get("text") or ""
        sqlite_without_binary = _sqlite_has_no_binary(runtime_root / "agent.db")
        downloaded_hash_matches = (
            hashlib.sha256(downloaded.content).hexdigest() == metadata["sha256"]
        )
        try:
            parsed_answer = json.loads(assistant_text)
        except json.JSONDecodeError:
            parsed_answer = None
        vision_understood = parsed_answer == {"left": "red", "right": "blue"}
        negative_cases = {
            "disguised_extension_status": disguised.status_code,
            "disguised_extension_error": disguised.json().get("error", {}).get("code"),
            "oversized_status": oversized.status_code,
            "oversized_error": oversized.json().get("error", {}).get("code"),
        }
        vision_run = {
            "upload_status": upload.status_code,
            "file_id": file_id,
            "question": QUESTION,
            "statuses_observed": statuses,
            "terminal_status": terminal["status"],
            "assistant_text": assistant_text,
            "error": terminal.get("error"),
        }
        validation = {
            "session": 2,
            "passed": (
                health["status"] == "ok"
                and upload.status_code == 201
                and metadata["mime_type"] == "image/png"
                and metadata["width"] == 128
                and metadata["height"] == 64
                and downloaded_hash_matches
                and disguised.status_code == 400
                and disguised.json()["error"]["code"] == "FILE_TYPE_UNSUPPORTED"
                and oversized.status_code == 400
                and oversized.json()["error"]["code"] == "FILE_TOO_LARGE"
                and terminal["status"] == "succeeded"
                and vision_understood
                and _has_legal_status_sequence(statuses)
                and sqlite_without_binary
            ),
            "provider_protocol": "openai-compatible-chat-completions-vision-data-url",
            "vision_understood": vision_understood,
            "downloaded_hash_matches": downloaded_hash_matches,
            "sqlite_without_binary": sqlite_without_binary,
        }
        _write_json(staging_root / "api-tests" / "vision-run.json", vision_run)
        _write_json(staging_root / "api-tests" / "negative-cases.json", negative_cases)
        _write_json(
            staging_root / "files" / "input-image-metadata.json",
            {
                key: metadata[key]
                for key in (
                    "file_id",
                    "source",
                    "purpose",
                    "mime_type",
                    "size_bytes",
                    "sha256",
                    "width",
                    "height",
                )
            },
        )
        (staging_root / "files" / "input-image.sha256.txt").write_text(
            f"{hashlib.sha256(png).hexdigest()}  generated-test-image.png\n",
            encoding="utf-8",
        )
        _write_json(staging_root / "validation-report.json", validation)
        (staging_root / "deployment-config.redacted.txt").write_text(
            "HOST=<local>\nPORT=<local>\nCHAT_BASE_URL=<redacted>\n"
            "CHAT_API_KEY=<redacted>\nCHAT_MODEL=<configured>\n"
            "PILOT_API_KEY=<ephemeral>\nMAX_UPLOAD_BYTES=10485760\n"
            "MAX_IMAGE_DIMENSION=4096\nMAX_IMAGE_PIXELS=20000000\n",
            encoding="utf-8",
        )
        (staging_root / "versions.txt").write_text(
            f"python={sys.version.split()[0]}\nservice={settings.service_version}\n",
            encoding="utf-8",
        )
        (staging_root / "README.md").write_text(
            "# Agent Service 会话 2 验收证据\n\n"
            "本目录记录真实 PNG 上传、file_id Vision Run、鉴权下载、伪装格式、"
            "超大上传和 SQLite 二进制隔离的脱敏结果。测试图片由脚本程序化生成，"
            "左半边为红色，右半边为蓝色。目录不包含密钥、完整 Authorization Header、"
            "SQLite 文件、原始图片、Base64、Provider 原始响应或服务器私有路径。\n",
            encoding="utf-8",
        )
        _redact_server_log(server_log)
        _scan_evidence(
            staging_root,
            [verification_key, settings.chat_api_key, settings.pilot_api_key],
        )
        if validation["passed"]:
            staging_root.replace(EVIDENCE_ROOT)
            published = True
        print(json.dumps(validation, ensure_ascii=False))
        return 0 if validation["passed"] else 1
    finally:
        if not published:
            shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(runtime_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
