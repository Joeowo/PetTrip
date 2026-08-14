"""校验并发布会话 7 的跨设备 API 验收证据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

PILOT_ROOT = Path(__file__).resolve().parents[1]
REMOTE_CLIENT = PILOT_ROOT / "remote_client" / "session7_remote_acceptance.ps1"
EVIDENCE_ROOT = PILOT_ROOT / "runs" / "pilot-cross-network-001"
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
QUICK_TUNNEL_HOST = re.compile(r"^[a-z0-9-]+\.trycloudflare\.com$")
LEGACY_REMOTE_CLIENT_SHA256 = (
    "a9ffc1dec9521e227a5bca36bd7317e3d7fae2694a90c1b55b2e67e312070b2e"
)
IDENTIFIER_PATTERNS = {
    "session_id": re.compile(r"^session_[A-Za-z0-9_]+$"),
    "file_id": re.compile(r"^file_[A-Za-z0-9_]+$"),
    "run_id": re.compile(r"^run_[A-Za-z0-9_]+$"),
    "request_id": re.compile(r"^req_[A-Za-z0-9_]+$"),
}


def source_sha256(path: Path) -> str:
    """计算不受 UTF-8 BOM 和 Git 行尾转换影响的源码哈希。"""
    content = path.read_bytes()
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    normalized = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"远程验收报告字段不合法：{name}。")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], name: str
) -> None:
    if set(value) != expected:
        raise ValueError(f"远程验收报告字段不合法：{name}。")


def _require_identifier(value: Any, kind: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER_PATTERNS[kind].fullmatch(value):
        raise ValueError(f"远程验收报告标识符不合法：{kind}。")


def _require_http_case(
    value: Any,
    *,
    name: str,
    status: int,
    error_code: str,
) -> None:
    item = _require_mapping(value, name)
    _require_exact_keys(item, {"http_status", "error_code", "request_id"}, name)
    if item["http_status"] != status or item["error_code"] != error_code:
        raise ValueError(f"远程验收报告负例不符合契约：{name}。")
    _require_identifier(item["request_id"], "request_id")


def _require_execution_time(value: Any) -> None:
    if not isinstance(value, str):
        raise ValueError("远程验收报告执行时间不合法。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("远程验收报告执行时间不合法。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("远程验收报告执行时间必须使用 UTC。")


def _has_legal_status_sequence(statuses: Any) -> bool:
    return statuses in (
        ["queued", "succeeded"],
        ["queued", "running", "succeeded"],
    )


def validate_quick_tunnel_url(value: str) -> str:
    """返回规范化 Quick Tunnel URL，并拒绝其他目的地。"""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not parsed.hostname
        or not QUICK_TUNNEL_HOST.fullmatch(parsed.hostname.lower())
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("公网入口不是受信任的 Cloudflare Quick Tunnel HTTPS 根地址。")
    return f"https://{parsed.hostname.lower()}"


def validate_remote_report(report: Any) -> dict[str, Any]:
    """验证远程设备白名单报告并返回 API 范围结论。"""
    root = _require_mapping(report, "root")
    _require_exact_keys(
        root,
        {
            "schema_version",
            "session",
            "scope",
            "producer",
            "operator_attested_external_device",
            "executed_at_utc",
            "remote_client_sha256",
            "public_transport",
            "authentication",
            "session_request",
            "upload",
            "run",
            "download",
            "errors",
            "remote_api_scope_passed",
        },
        "root",
    )
    schema_version = root["schema_version"]
    if (
        schema_version not in {"1.1", "1.2"}
        or root["session"] != 7
        or root["scope"] != "remote_agent_api"
        or root["producer"] != "pettrip_session7_powershell_client"
        or root["operator_attested_external_device"] is not True
        or root["remote_api_scope_passed"] is not True
    ):
        raise ValueError("远程验收报告根结论不符合会话 7 API 范围。")
    _require_execution_time(root["executed_at_utc"])
    expected_client_hash = (
        LEGACY_REMOTE_CLIENT_SHA256
        if schema_version == "1.1"
        else source_sha256(REMOTE_CLIENT)
    )
    if root["remote_client_sha256"] != expected_client_hash:
        raise ValueError("远程验收报告的客户端脚本哈希不匹配。")

    transport = _require_mapping(root["public_transport"], "public_transport")
    _require_exact_keys(
        transport,
        {
            "scheme",
            "base_url_sha256",
            "tls_validation_enabled",
            "redirects_followed",
        },
        "public_transport",
    )
    if (
        transport["scheme"] != "https"
        or not isinstance(transport["base_url_sha256"], str)
        or not HEX_SHA256.fullmatch(transport["base_url_sha256"])
        or transport["tls_validation_enabled"] is not True
        or transport["redirects_followed"] is not False
    ):
        raise ValueError("远程验收报告公网 HTTPS 配置不合法。")

    authentication = _require_mapping(root["authentication"], "authentication")
    _require_exact_keys(authentication, {"missing_key", "wrong_key"}, "authentication")
    _require_http_case(
        authentication["missing_key"],
        name="authentication.missing_key",
        status=401,
        error_code="AUTHENTICATION_FAILED",
    )
    _require_http_case(
        authentication["wrong_key"],
        name="authentication.wrong_key",
        status=401,
        error_code="AUTHENTICATION_FAILED",
    )

    session_request = _require_mapping(root["session_request"], "session_request")
    _require_exact_keys(
        session_request,
        {"http_status", "session_id", "request_id"},
        "session_request",
    )
    if session_request["http_status"] != 201:
        raise ValueError("远程验收报告 Session 创建失败。")
    _require_identifier(session_request["session_id"], "session_id")
    _require_identifier(session_request["request_id"], "request_id")

    upload = _require_mapping(root["upload"], "upload")
    upload_keys = {
        "http_status",
        "file_id",
        "request_id",
        "mime_type",
        "width",
        "height",
        "size_bytes",
        "sha256",
    }
    if schema_version == "1.2":
        upload_keys.update({"source", "purpose"})
    _require_exact_keys(upload, upload_keys, "upload")
    if (
        upload["http_status"] != 201
        or (
            schema_version == "1.2"
            and (
                upload["source"] != "user_upload"
                or upload["purpose"] != "vision_input"
            )
        )
        or upload["mime_type"] != "image/png"
        or upload["width"] != 128
        or upload["height"] != 64
        or not isinstance(upload["size_bytes"], int)
        or upload["size_bytes"] <= 0
        or not isinstance(upload["sha256"], str)
        or not HEX_SHA256.fullmatch(upload["sha256"])
    ):
        raise ValueError("远程验收报告图片上传结果不合法。")
    _require_identifier(upload["file_id"], "file_id")
    _require_identifier(upload["request_id"], "request_id")

    run = _require_mapping(root["run"], "run")
    _require_exact_keys(
        run,
        {
            "create_http_status",
            "run_id",
            "create_request_id",
            "statuses_observed",
            "terminal_status",
            "terminal_request_id",
            "vision_answer",
        },
        "run",
    )
    vision_answer = _require_mapping(run["vision_answer"], "run.vision_answer")
    _require_exact_keys(vision_answer, {"left", "right"}, "run.vision_answer")
    if (
        run["create_http_status"] != 202
        or run["terminal_status"] != "succeeded"
        or not _has_legal_status_sequence(run["statuses_observed"])
        or dict(vision_answer) != {"left": "red", "right": "blue"}
    ):
        raise ValueError("远程验收报告 Run 或 Vision 结果不合法。")
    _require_identifier(run["run_id"], "run_id")
    _require_identifier(run["create_request_id"], "request_id")
    _require_identifier(run["terminal_request_id"], "request_id")

    download = _require_mapping(root["download"], "download")
    _require_exact_keys(
        download,
        {"http_status", "sha256", "matches_source", "matches_metadata"},
        "download",
    )
    if (
        download["http_status"] != 200
        or download["sha256"] != upload["sha256"]
        or download["matches_source"] is not True
        or download["matches_metadata"] is not True
    ):
        raise ValueError("远程验收报告文件下载哈希不一致。")

    errors = _require_mapping(root["errors"], "errors")
    _require_exact_keys(
        errors,
        {"missing_idempotency_key", "missing_resource", "unauthorized_download"},
        "errors",
    )
    _require_http_case(
        errors["missing_idempotency_key"],
        name="errors.missing_idempotency_key",
        status=400,
        error_code="VALIDATION_ERROR",
    )
    _require_http_case(
        errors["missing_resource"],
        name="errors.missing_resource",
        status=404,
        error_code="RESOURCE_NOT_FOUND",
    )
    _require_http_case(
        errors["unauthorized_download"],
        name="errors.unauthorized_download",
        status=401,
        error_code="AUTHENTICATION_FAILED",
    )

    return {
        "session": 7,
        "scope": "remote_agent_api",
        "remote_api_scope_passed": True,
        "operator_attested_external_device": True,
        "public_https_confirmed": True,
        "authentication_negative_cases_passed": True,
        "session_created": True,
        "real_png_uploaded": True,
        "vision_run_succeeded": True,
        "run_status_sequence_legal": True,
        "download_hash_matches": True,
        "stable_error_contract_confirmed": True,
        "unity_required_for_current_scope": False,
        "unity_executed": False,
        "full_unity_cross_network_demo_passed": False,
    }


def scan_evidence(root: Path, *, secrets_to_reject: list[str]) -> None:
    """拒绝密钥、完整入口、私有路径和二进制进入证据。"""
    patterns = [
        re.compile(r"authorization\s*:\s*bearer", re.IGNORECASE),
        re.compile(r"\bbearer\s+(?!key\b)[A-Za-z0-9._~-]+", re.IGNORECASE),
        re.compile(r"https?://", re.IGNORECASE),
        re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])", re.IGNORECASE),
        re.compile(r"data:image/", re.IGNORECASE),
        re.compile(r"[A-Za-z]:[\\/]"),
        re.compile(r"/(?:home|users|private|root|srv|var|tmp|opt)/", re.IGNORECASE),
        re.compile(r"pettrip_pilot_[A-Za-z0-9_-]+"),
        re.compile(r"cf-access-client-(?:id|secret)", re.IGNORECASE),
        re.compile(r"(?:set-)?cookie\s*:", re.IGNORECASE),
    ]
    secrets = [value for value in secrets_to_reject if value]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"证据脱敏扫描失败：{path.name} 不是 UTF-8 文本。") from exc
        if "\x00" in text or any(pattern.search(text) for pattern in patterns) or any(
            secret in text for secret in secrets
        ):
            raise ValueError(f"证据脱敏扫描失败：{path.name}。")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _versions() -> str:
    files = {
        "run_session7_server_sha256": Path(__file__).with_name(
            "run_session7_server.py"
        ),
        "run_session7_tunnel_sha256": Path(__file__).with_name(
            "run_session7_tunnel.py"
        ),
        "verify_session7_sha256": Path(__file__),
        "remote_client_sha256": REMOTE_CLIENT,
    }
    lines = [f"python={sys.version.split()[0]}", f"pydantic={version('pydantic')}"]
    lines.extend(f"{name}={source_sha256(path)}" for name, path in files.items())
    return "\n".join(lines) + "\n"


def publish_evidence(
    *,
    report_path: Path,
    base_url_path: Path,
    secrets_to_reject: list[str],
    origin_listener_loopback_only: bool,
) -> dict[str, Any]:
    """验证报告和实际入口，并原子发布 API 范围证据。"""
    if EVIDENCE_ROOT.exists():
        raise FileExistsError(f"证据目录已存在：{EVIDENCE_ROOT.name}")
    if origin_listener_loopback_only is not True:
        raise ValueError("会话 7 要求 origin 仅监听本机回环地址。")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        actual_url = validate_quick_tunnel_url(
            base_url_path.read_text(encoding="utf-8").strip()
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("远程报告或公网入口文件不可读取。") from exc

    validation = validate_remote_report(report)
    actual_url_hash = hashlib.sha256(actual_url.encode("utf-8")).hexdigest()
    if report["public_transport"]["base_url_sha256"] != actual_url_hash:
        raise ValueError("远程验收报告的公网入口哈希不匹配。")
    validation.update(
        {
            "origin_listener_loopback_only": True,
            "edge_provider": "cloudflare",
            "actual_entry_hash_matches_report": True,
            "remote_client_hash_matches": True,
            "evidence_redaction_passed": True,
        }
    )

    EVIDENCE_ROOT.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".pilot-session7-", dir=EVIDENCE_ROOT.parent)
    )
    published = False
    try:
        _write_json(staging / "remote-client-run.json", report)
        _write_json(staging / "validation-report.json", validation)
        (staging / "remote-file-hash.txt").write_text(
            f"{report['download']['sha256']}  remote-upload-download.png\n",
            encoding="utf-8",
            newline="\n",
        )
        (staging / "https-endpoint.redacted.txt").write_text(
            "edge_provider=cloudflare\n"
            "public_scheme=https\n"
            "public_hostname=<redacted>\n"
            f"public_base_url_sha256={actual_url_hash}\n"
            "origin_host=<loopback>\n"
            "origin_port=<redacted>\n"
            "origin_listener_loopback_only=true\n",
            encoding="utf-8",
            newline="\n",
        )
        (staging / "versions.txt").write_text(
            _versions(), encoding="utf-8", newline="\n"
        )
        (staging / "README.md").write_text(
            "# Agent Service 会话 7 远程 API 验收证据\n\n"
            "本目录记录操作员在另一台非服务端 Windows 设备上，通过公网 HTTPS 完成"
            "鉴权负例、Session、真实 PNG 上传、Vision Run 轮询、稳定错误响应、"
            "鉴权下载和设备端 SHA-256 校验的白名单证据。\n\n"
            "当前范围不要求 Unity，因此只声明远程 Agent API 范围通过，不声明完整 Unity "
            "跨网络演示完成。完整入口、Bearer Key、Provider Key、本地端口、服务器路径、"
            "原始图片和原始请求日志均未保存。\n",
            encoding="utf-8",
            newline="\n",
        )
        scan_evidence(staging, secrets_to_reject=secrets_to_reject)
        staging.replace(EVIDENCE_ROOT)
        published = True
        return validation
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def _load_secret_values() -> list[str]:
    values = [
        os.environ.get(name, "").strip()
        for name in ("PILOT_API_KEY", "CHAT_API_KEY", "IMAGES_API_KEY")
    ]
    for path_name in ("PETTRIP_PILOT_KEY_PATH", "PETTRIP_LOCAL_ENV_PATH"):
        path_value = os.environ.get(path_name, "").strip()
        if not path_value:
            continue
        path = Path(path_value).expanduser()
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if path_name == "PETTRIP_PILOT_KEY_PATH":
                values.append(line)
            elif line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key.strip().endswith("_API_KEY"):
                    values.append(value.strip().strip('"').strip("'"))
    return [value for value in values if value]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="发布会话 7 远程 API 验收证据。")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--base-url-file", required=True, type=Path)
    parser.add_argument("--origin-loopback-confirmed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        validation = publish_evidence(
            report_path=args.report,
            base_url_path=args.base_url_file,
            secrets_to_reject=_load_secret_values(),
            origin_listener_loopback_only=args.origin_loopback_confirmed,
        )
    except (FileExistsError, ValueError) as exc:
        print(f"会话 7 证据未发布：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(validation, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
