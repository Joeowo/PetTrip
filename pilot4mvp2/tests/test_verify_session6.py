from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pilot4mvp2.scripts import verify_session6


def test_session6_publishes_only_passed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "published"
    staging.mkdir()
    monkeypatch.setattr(verify_session6, "EVIDENCE_ROOT", destination)

    assert verify_session6._publish(staging, {"passed": False}) is False
    assert staging.is_dir()
    assert not destination.exists()

    assert verify_session6._publish(staging, {"passed": True}) is True
    assert destination.is_dir()
    assert not staging.exists()


def test_session6_publish_refuses_to_overwrite_existing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "published"
    staging.mkdir()
    destination.mkdir()
    (destination / "existing.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(verify_session6, "EVIDENCE_ROOT", destination)

    with pytest.raises(FileExistsError, match="证据目录已存在"):
        verify_session6._publish(staging, {"passed": True})

    assert staging.is_dir()
    assert (destination / "existing.txt").read_text(encoding="utf-8") == "keep"


def test_session6_source_hash_is_stable_across_line_endings(tmp_path: Path) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b"first\nsecond\n")
    crlf.write_bytes(b"first\r\nsecond\r\n")

    assert verify_session6._source_sha256(lf) == verify_session6._source_sha256(crlf)


def test_session6_evidence_scan_accepts_redacted_markers(tmp_path: Path) -> None:
    (tmp_path / "deployment-config.redacted.txt").write_text(
        "CHAT_API_KEY=<redacted>\nIMAGE_API_KEY=<redacted>\n"
        "PILOT_API_KEY=<redacted>\n",
        encoding="utf-8",
    )

    verify_session6._scan_evidence(tmp_path, runtime_root=tmp_path / "runtime")


@pytest.mark.parametrize(
    "leaked_value",
    [
        "Authorization: Bearer hidden-token",
        "data:image/png;base64,AAAA",
        verify_session6.PILOT_KEY,
        "controlled-chat-key",
        "controlled-image-key",
        str(verify_session6.REPO_ROOT),
    ],
)
def test_session6_evidence_scan_rejects_sensitive_values(
    tmp_path: Path, leaked_value: str
) -> None:
    (tmp_path / "evidence.json").write_text(leaked_value, encoding="utf-8")

    with pytest.raises(RuntimeError, match="脱敏扫描失败"):
        verify_session6._scan_evidence(tmp_path, runtime_root=tmp_path / "runtime")


def test_session6_cross_process_recovery_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_root = tmp_path / "pilot-multimodal-agent-session6-001"
    monkeypatch.setattr(verify_session6, "EVIDENCE_ROOT", evidence_root)
    monkeypatch.setattr(verify_session6, "REPO_ROOT", Path(__file__).resolve().parents[2])

    result = verify_session6.main()

    assert result == 0
    report = json.loads(
        (evidence_root / "api-tests" / "recovery-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["passed"] is True
    assert report["same_session_two_rounds"] is True
    assert report["completed_results_restored"] is True
    assert report["recovery_history_valid"] is True
    assert report["image_metadata_matches_download"] is True
    assert report["interrupted_run_status_after_restart"] == "failed"
    assert report["interrupted_run_error"]["code"] == "SERVICE_RESTARTED"
    assert report["interrupted_run_events_after_restart"] == [
        "run.queued",
        "run.started",
        "run.failed",
    ]
    assert report["new_run_after_restart_status"] == "succeeded"
    assert report["provider_calls_for_interrupted_run_before_recovery"] == 1
    assert report["provider_calls_for_interrupted_run_after_recovery"] == 1
    provider_log = evidence_root / report["provider_call_log"]["path"]
    assert provider_log.is_file()
    assert hashlib.sha256(provider_log.read_bytes()).hexdigest() == report[
        "provider_call_log"
    ]["sha256"]
    assert json.loads(
        (evidence_root / "validation-report.json").read_text(encoding="utf-8")
    ) == report


def test_session6_acceptance_runs_concurrently_without_port_conflicts(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    processes: list[tuple[subprocess.Popen[str], Path]] = []
    try:
        for index in range(2):
            evidence_root = tmp_path / f"concurrent-{index}"
            env = dict(os.environ)
            env.update(
                {
                    "PYTHONPATH": str(repo_root),
                    "SESSION6_EVIDENCE_ROOT": str(evidence_root),
                }
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "pilot4mvp2.scripts.verify_session6"],
                cwd=repo_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            processes.append((process, evidence_root))

        for process, evidence_root in processes:
            stdout, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
            report = json.loads(
                (evidence_root / "validation-report.json").read_text(
                    encoding="utf-8"
                )
            )
            assert report["passed"] is True
    finally:
        for process, _ in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=8)
