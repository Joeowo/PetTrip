from __future__ import annotations

from pathlib import Path

import pytest

from pilot4mvp2.scripts.verify_session5 import (
    _publish_evidence,
    _run_controlled_failure,
    _scan_evidence,
    _sha256,
)


def test_session5_source_hash_is_stable_across_line_endings(tmp_path: Path) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b"first\nsecond\n")
    crlf.write_bytes(b"first\r\nsecond\r\n")

    assert _sha256(lf) == _sha256(crlf)


def test_failed_session5_validation_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "published"
    staging.mkdir()
    monkeypatch.setattr(
        "pilot4mvp2.scripts.verify_session5.EVIDENCE_ROOT", destination
    )

    published = _publish_evidence(staging, {"passed": False})

    assert published is False
    assert staging.is_dir()
    assert not destination.exists()


def test_passed_session5_validation_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "published"
    staging.mkdir()
    monkeypatch.setattr(
        "pilot4mvp2.scripts.verify_session5.EVIDENCE_ROOT", destination
    )

    published = _publish_evidence(staging, {"passed": True})

    assert published is True
    assert destination.is_dir()
    assert not staging.exists()


def test_session5_evidence_scan_accepts_redacted_markers(tmp_path: Path) -> None:
    evidence = tmp_path / "deployment-config.redacted.txt"
    evidence.write_text(
        "CHAT_API_KEY=<redacted>\nIMAGE_API_KEY=<redacted>\n"
        "PILOT_API_KEY=<ephemeral>\n",
        encoding="utf-8",
    )

    _scan_evidence(
        tmp_path,
        ["real-chat-secret", "real-image-secret", "real-pilot-secret"],
    )


@pytest.mark.parametrize(
    "leaked_value",
    [
        "Authorization: Bearer hidden-token",
        "data:image/png;base64,AAAA",
        "real-chat-secret",
        "real-image-secret",
        "real-pilot-secret",
    ],
)
def test_session5_evidence_scan_rejects_sensitive_values(
    tmp_path: Path, leaked_value: str
) -> None:
    (tmp_path / "evidence.json").write_text(leaked_value, encoding="utf-8")

    with pytest.raises(RuntimeError, match="脱敏扫描失败"):
        _scan_evidence(
            tmp_path,
            ["real-chat-secret", "real-image-secret", "real-pilot-secret"],
        )


@pytest.mark.parametrize(
    ("case", "expected_code", "expected_calls", "expected_events"),
    [
        (
            "structured_failure",
            "STRUCTURED_OUTPUT_INVALID",
            {"structured": 1, "text": 0, "image": 0},
            ["run.queued", "run.started", "run.failed"],
        ),
        (
            "text_failure",
            "CHAT_PROVIDER_UNAVAILABLE",
            {"structured": 1, "text": 1, "image": 0},
            ["run.queued", "run.started", "run.failed"],
        ),
        (
            "image_failure",
            "IMAGE_PROVIDER_UNAVAILABLE",
            {"structured": 1, "text": 1, "image": 1},
            [
                "run.queued",
                "run.started",
                "image_generation.started",
                "run.failed",
            ],
        ),
    ],
)
def test_controlled_session5_failures_never_commit_partial_output(
    tmp_path: Path,
    case: str,
    expected_code: str,
    expected_calls: dict[str, int],
    expected_events: list[str],
) -> None:
    result = _run_controlled_failure(case, tmp_path / case)

    assert result["terminal_status"] == "failed"
    assert result["error"]["code"] == expected_code
    assert result["output_present"] is False
    assert result["message_roles"] == ["user"]
    assert result["assistant_messages_persisted"] == 0
    assert result["generated_file_records"] == 0
    assert result["output_relations"] == 0
    assert result["generated_files_on_disk"] == 0
    assert result["provider_calls"] == expected_calls
    assert result["events"] == expected_events
