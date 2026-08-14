from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot4mvp2.scripts import run_session7_server, verify_session7

PILOT_ROOT = Path(__file__).resolve().parents[1]
REMOTE_SCRIPT = PILOT_ROOT / "remote_client" / "session7_remote_acceptance.ps1"
PUBLISHED_REMOTE_REPORT = (
    PILOT_ROOT / "runs" / "pilot-cross-network-001" / "remote-client-run.json"
)
TEST_BASE_URL = "https://pilot-test.trycloudflare.com"


@pytest.fixture
def valid_remote_report() -> dict[str, object]:
    source_hash = "a" * 64
    return {
        "schema_version": "1.2",
        "session": 7,
        "scope": "remote_agent_api",
        "producer": "pettrip_session7_powershell_client",
        "operator_attested_external_device": True,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "remote_client_sha256": verify_session7.source_sha256(REMOTE_SCRIPT),
        "public_transport": {
            "scheme": "https",
            "base_url_sha256": hashlib.sha256(
                TEST_BASE_URL.encode("utf-8")
            ).hexdigest(),
            "tls_validation_enabled": True,
            "redirects_followed": False,
        },
        "authentication": {
            "missing_key": {
                "http_status": 401,
                "error_code": "AUTHENTICATION_FAILED",
                "request_id": "req_missing",
            },
            "wrong_key": {
                "http_status": 401,
                "error_code": "AUTHENTICATION_FAILED",
                "request_id": "req_wrong",
            },
        },
        "session_request": {
            "http_status": 201,
            "session_id": "session_remote",
            "request_id": "req_session",
        },
        "upload": {
            "http_status": 201,
            "file_id": "file_remote",
            "request_id": "req_upload",
            "source": "user_upload",
            "purpose": "vision_input",
            "mime_type": "image/png",
            "width": 128,
            "height": 64,
            "size_bytes": 256,
            "sha256": source_hash,
        },
        "run": {
            "create_http_status": 202,
            "run_id": "run_remote",
            "create_request_id": "req_run_create",
            "statuses_observed": ["queued", "running", "succeeded"],
            "terminal_status": "succeeded",
            "terminal_request_id": "req_run_terminal",
            "vision_answer": {"left": "red", "right": "blue"},
        },
        "download": {
            "http_status": 200,
            "sha256": source_hash,
            "matches_source": True,
            "matches_metadata": True,
        },
        "errors": {
            "missing_idempotency_key": {
                "http_status": 400,
                "error_code": "VALIDATION_ERROR",
                "request_id": "req_missing_idempotency",
            },
            "missing_resource": {
                "http_status": 404,
                "error_code": "RESOURCE_NOT_FOUND",
                "request_id": "req_missing_resource",
            },
            "unauthorized_download": {
                "http_status": 401,
                "error_code": "AUTHENTICATION_FAILED",
                "request_id": "req_unauthorized_download",
            },
        },
        "remote_api_scope_passed": True,
    }


def test_session7_key_is_high_entropy_and_reused(tmp_path: Path) -> None:
    key_path = tmp_path / "pettrip-pilot-api-key.local"

    first = run_session7_server.load_or_create_pilot_key(key_path)
    second = run_session7_server.load_or_create_pilot_key(key_path)

    assert first == second
    assert first.startswith("pettrip_pilot_")
    assert len(first) >= 48
    assert key_path.read_text(encoding="utf-8").strip() == first


def test_session7_rejects_invalid_existing_key(tmp_path: Path) -> None:
    key_path = tmp_path / "pettrip-pilot-api-key.local"
    key_path.write_text("short\n", encoding="utf-8")

    with pytest.raises(ValueError, match="PetTrip Pilot API Key"):
        run_session7_server.load_or_create_pilot_key(key_path)


def test_session7_key_rotation_uses_a_new_runtime_directory(tmp_path: Path) -> None:
    first_key = "pettrip_pilot_" + "a" * 43
    second_key = "pettrip_pilot_" + "b" * 43

    first_runtime = run_session7_server.runtime_root_for_key(tmp_path, first_key)
    repeated_runtime = run_session7_server.runtime_root_for_key(tmp_path, first_key)
    second_runtime = run_session7_server.runtime_root_for_key(tmp_path, second_key)

    assert first_runtime == repeated_runtime
    assert first_runtime != second_runtime
    assert first_key not in str(first_runtime)
    assert second_key not in str(second_runtime)


@pytest.mark.parametrize(
    "value",
    ["relative", Path.cwd(), run_session7_server.PILOT_ROOT / "private"],
)
def test_session7_local_paths_must_be_absolute_and_outside_worktree(
    value: str | Path,
) -> None:
    with pytest.raises(ValueError, match="仓库外的绝对路径"):
        run_session7_server.resolve_external_path(Path(value), "测试路径")


def test_session7_accepts_external_absolute_path(tmp_path: Path) -> None:
    resolved = run_session7_server.resolve_external_path(tmp_path, "测试路径")

    assert resolved == tmp_path.resolve()


def test_session7_private_directory_uses_windows_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(arguments)
        environments.append(kwargs["env"])  # type: ignore[arg-type]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_session7_server.subprocess, "run", fake_run)
    run_session7_server.protect_private_directory(tmp_path)

    assert calls
    command = " ".join(calls[0]).lower()
    assert "powershell.exe" in command
    assert "setaccessruleprotection" in command
    assert "removeaccessrulespecific" in command
    assert "set-acl" in command
    assert str(tmp_path) not in calls[0]
    assert environments[0]["PETTRIP_PRIVATE_DIRECTORY"] == str(tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "http://provider.example/v1",
        "ftp://provider.example/v1",
        "provider.example/v1",
        "https://user:password@provider.example/v1",
    ],
)
def test_session7_server_requires_safe_https_provider_url(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        run_session7_server.validate_provider_url(url)


def test_session7_requires_image_key_when_image_base_url_is_explicit() -> None:
    with pytest.raises(ValueError, match="IMAGES_BASE_URL.*IMAGES_API_KEY"):
        run_session7_server.load_settings(
            overrides={
                "PILOT_API_KEY": "pettrip_pilot_" + "x" * 48,
                "CHAT_BASE_URL": "https://chat.example/v1",
                "CHAT_API_KEY": "chat-key",
                "CHAT_MODEL": "chat-model",
                "IMAGES_BASE_URL": "https://images.example/v1",
                "IMAGES_API_KEY": "",
                "DATA_DIR": "data-session7-test",
            }
        )


def test_session7_server_accepts_https_provider_url() -> None:
    run_session7_server.validate_provider_url("https://provider.example/v1")


def test_session7_final_settings_reject_http_image_provider() -> None:
    settings = SimpleNamespace(
        chat_base_url="https://chat.example/v1",
        image_base_url="http://image.example/v1",
    )

    with pytest.raises(ValueError, match="HTTPS"):
        run_session7_server.validate_final_provider_settings(settings)


def test_session7_uses_images_provider_keys_directly(tmp_path: Path) -> None:
    env_path = tmp_path / "provider.env"
    env_path.write_text(
        "CHAT_BASE_URL=https://chat.example/v1\n"
        "CHAT_API_KEY=chat-test-secret\n"
        "CHAT_MODEL=chat-model\n"
        "IMAGES_BASE_URL=https://images.example/v1\n"
        "IMAGES_API_KEY=image-test-secret\n"
        "IMAGES_MODEL=gpt-image-2\n",
        encoding="utf-8",
    )
    managed_keys = {
        "PETTRIP_LOCAL_ENV_PATH": str(env_path),
        "IMAGES_BASE_URL": "",
        "IMAGES_API_KEY": "",
        "IMAGES_MODEL": "",
        "CHAT_BASE_URL": "",
        "CHAT_API_KEY": "",
        "CHAT_MODEL": "",
    }
    original = {key: os.environ.get(key) for key in managed_keys}
    try:
        for key, value in managed_keys.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        overrides = run_session7_server._session7_overrides(
            tmp_path / "private-root", "pettrip_pilot_" + "x" * 48
        )
        settings = run_session7_server.load_settings(overrides=overrides)
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert overrides["IMAGES_BASE_URL"] == "https://images.example/v1"
    assert overrides["IMAGES_API_KEY"] == "image-test-secret"
    assert overrides["IMAGES_MODEL"] == "gpt-image-2"
    assert settings.image_base_url == "https://images.example/v1"
    assert settings.image_api_key == "image-test-secret"
    assert settings.image_model == "gpt-image-2"


def test_session7_server_suppresses_local_listener_logs() -> None:
    source = Path(run_session7_server.__file__).read_text(encoding="utf-8")

    assert 'host="127.0.0.1"' in source
    assert 'log_level="warning"' in source
    assert "access_log=False" in source


def test_session7_tunnel_launcher_keeps_addresses_out_of_logs() -> None:
    source = Path(run_session7_server.__file__).with_name(
        "run_session7_tunnel.py"
    ).read_text(encoding="utf-8")

    assert "subprocess.PIPE" in source
    assert "URL_PATTERN" in source
    assert "url_file.write_text" in source
    assert "print(line" not in source


def test_session7_validates_complete_remote_report(
    valid_remote_report: dict[str, object],
) -> None:
    validation = verify_session7.validate_remote_report(valid_remote_report)

    assert validation["remote_api_scope_passed"] is True
    assert validation["unity_required_for_current_scope"] is False
    assert validation["unity_executed"] is False
    assert "passed" not in validation


def test_session7_validates_published_legacy_report() -> None:
    report = json.loads(PUBLISHED_REMOTE_REPORT.read_text(encoding="utf-8"))

    validation = verify_session7.validate_remote_report(report)

    assert report["schema_version"] == "1.1"
    assert (
        report["remote_client_sha256"]
        == verify_session7.LEGACY_REMOTE_CLIENT_SHA256
    )
    assert validation["remote_api_scope_passed"] is True


def test_session7_rejects_legacy_report_with_different_client_hash() -> None:
    report = json.loads(PUBLISHED_REMOTE_REPORT.read_text(encoding="utf-8"))
    report["remote_client_sha256"] = verify_session7.source_sha256(REMOTE_SCRIPT)

    with pytest.raises(ValueError, match="客户端脚本哈希"):
        verify_session7.validate_remote_report(report)


def test_session7_rejects_current_report_without_upload_provenance(
    valid_remote_report: dict[str, object],
) -> None:
    report = copy.deepcopy(valid_remote_report)
    del report["upload"]["source"]  # type: ignore[index]

    with pytest.raises(ValueError, match="字段不合法：upload"):
        verify_session7.validate_remote_report(report)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "1.3"),
        (("operator_attested_external_device",), False),
        (("public_transport", "scheme"), "http"),
        (("public_transport", "tls_validation_enabled"), False),
        (("public_transport", "redirects_followed"), True),
        (("authentication", "wrong_key", "http_status"), 200),
        (("upload", "mime_type"), "text/plain"),
        (("run", "terminal_status"), "failed"),
        (("run", "vision_answer", "right"), "green"),
        (("download", "matches_source"), False),
        (("errors", "missing_resource", "error_code"), "INTERNAL_ERROR"),
        (("remote_api_scope_passed",), False),
    ],
)
def test_session7_rejects_incomplete_or_failed_remote_report(
    valid_remote_report: dict[str, object],
    path: tuple[str, ...],
    value: object,
) -> None:
    report = copy.deepcopy(valid_remote_report)
    target = report
    for key in path[:-1]:
        target = target[key]  # type: ignore[index, assignment]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError, match="远程验收报告"):
        verify_session7.validate_remote_report(report)


@pytest.mark.parametrize(
    "statuses",
    [
        ["queued", "failed", "succeeded"],
        ["queued", "running", "failed", "succeeded"],
        ["running", "succeeded"],
        ["queued", "queued", "succeeded"],
    ],
)
def test_session7_rejects_impossible_status_sequences(
    valid_remote_report: dict[str, object], statuses: list[str]
) -> None:
    report = copy.deepcopy(valid_remote_report)
    report["run"]["statuses_observed"] = statuses  # type: ignore[index]

    with pytest.raises(ValueError, match="远程验收报告"):
        verify_session7.validate_remote_report(report)


@pytest.mark.parametrize(
    "statuses",
    [["queued", "succeeded"], ["queued", "running", "succeeded"]],
)
def test_session7_accepts_only_real_success_sequences(
    valid_remote_report: dict[str, object], statuses: list[str]
) -> None:
    report = copy.deepcopy(valid_remote_report)
    report["run"]["statuses_observed"] = statuses  # type: ignore[index]

    assert verify_session7.validate_remote_report(report)[
        "remote_api_scope_passed"
    ] is True


@pytest.mark.parametrize(
    "leak",
    [
        "Authorization: Bearer secret",
        "Bearer secret",
        "https://hidden.trycloudflare.com",
        "http://127.0.0.1:8001",
        "data:image/png;base64,AAAA",
        r"C:\\private\\agent.db",
        "/home/private/agent.db",
        "pettrip_pilot_real_secret_value",
    ],
)
def test_session7_evidence_scan_rejects_sensitive_content(
    tmp_path: Path, leak: str
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "report.txt").write_text(leak, encoding="utf-8")

    with pytest.raises(ValueError, match="脱敏扫描失败"):
        verify_session7.scan_evidence(
            evidence,
            secrets_to_reject=["pettrip_pilot_real_secret_value"],
        )


def test_session7_publish_compares_actual_url_and_script_hash(
    tmp_path: Path,
    valid_remote_report: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "remote-report.json"
    report_path.write_text(json.dumps(valid_remote_report), encoding="utf-8")
    base_url_path = tmp_path / "public-base-url.local"
    base_url_path.write_text(f"{TEST_BASE_URL}\n", encoding="utf-8")
    destination = tmp_path / "pilot-cross-network-001"
    monkeypatch.setattr(verify_session7, "EVIDENCE_ROOT", destination)

    validation = verify_session7.publish_evidence(
        report_path=report_path,
        base_url_path=base_url_path,
        secrets_to_reject=["hidden-secret"],
        origin_listener_loopback_only=True,
    )

    assert validation["remote_api_scope_passed"] is True
    assert validation["full_unity_cross_network_demo_passed"] is False
    assert destination.is_dir()
    assert not (destination / "unity-connectivity-report.json").exists()


def test_session7_publish_rejects_different_tunnel_url(
    tmp_path: Path,
    valid_remote_report: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "remote-report.json"
    report_path.write_text(json.dumps(valid_remote_report), encoding="utf-8")
    base_url_path = tmp_path / "public-base-url.local"
    base_url_path.write_text(
        "https://different.trycloudflare.com\n", encoding="utf-8"
    )
    monkeypatch.setattr(verify_session7, "EVIDENCE_ROOT", tmp_path / "evidence")

    with pytest.raises(ValueError, match="入口哈希"):
        verify_session7.publish_evidence(
            report_path=report_path,
            base_url_path=base_url_path,
            secrets_to_reject=[],
            origin_listener_loopback_only=True,
        )


def test_session7_source_hash_is_stable_across_line_endings_and_bom(
    tmp_path: Path,
) -> None:
    lf_path = tmp_path / "lf.txt"
    crlf_bom_path = tmp_path / "crlf-bom.txt"
    lf_path.write_bytes(b"first\nsecond\n")
    crlf_bom_path.write_bytes(b"\xef\xbb\xbffirst\r\nsecond\r\n")

    assert verify_session7.source_sha256(lf_path) == verify_session7.source_sha256(
        crlf_bom_path
    )


def test_session7_remote_powershell_client_uses_utf8_bom() -> None:
    assert REMOTE_SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")


def test_session7_remote_powershell_client_has_security_guards() -> None:
    script = REMOTE_SCRIPT.read_text(encoding="utf-8")

    assert "ConfirmExternalDevice" in script
    assert "BaseUrlPath" in script
    assert "trycloudflare" in script
    assert "^[a-z0-9-]+\\.trycloudflare\\.com$" in script
    assert "IsDefaultPort" in script
    assert "Protect-PrivateFile" in script
    assert "remote_client_sha256" in script
    assert "assistant_text" not in script
    assert "AllowAutoRedirect = $false" in script
    assert "SkipCertificateCheck" not in script
    assert "DangerousAcceptAnyServerCertificateValidator" not in script
    assert "cloudflared" not in script.lower()
    assert "run_server" not in script.lower()
    assert "127.0.0.1" not in script
    assert "localhost" not in script.lower()
    assert "Authorization: Bearer" not in script


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid",
        "https://example.com",
        "https://fake.trycloudflare.com.evil.example",
        "https://valid.trycloudflare.com:8443",
        "https://valid.trycloudflare.com/path",
    ],
)
def test_session7_remote_client_rejects_untrusted_destination_before_report(
    tmp_path: Path, url: str
) -> None:
    base_url_path = tmp_path / "base-url.local"
    key_path = tmp_path / "pilot-key.local"
    report_path = tmp_path / "report.json"
    base_url_path.write_text(f"{url}\n", encoding="utf-8")
    key_path.write_text(
        f"pettrip_pilot_{'x' * 48}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(REMOTE_SCRIPT),
            "-BaseUrlPath",
            str(base_url_path),
            "-ApiKeyPath",
            str(key_path),
            "-OutputPath",
            str(report_path),
            "-ConfirmExternalDevice",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert not report_path.exists()
    assert "pettrip_pilot_" not in result.stdout
    assert "pettrip_pilot_" not in result.stderr
    assert url not in result.stdout
    assert url not in result.stderr
