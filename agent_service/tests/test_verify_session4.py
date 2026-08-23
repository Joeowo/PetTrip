from __future__ import annotations

from pathlib import Path

import pytest

from agent_service.scripts.verify_session4 import _publish_evidence, _scan_evidence, _sha256


def test_session4_source_hash_is_stable_across_line_endings(tmp_path: Path) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b"first\nsecond\n")
    crlf.write_bytes(b"first\r\nsecond\r\n")

    assert _sha256(lf) == _sha256(crlf)


def test_failed_session4_validation_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "published"
    staging.mkdir()
    monkeypatch.setattr(
        "agent_service.scripts.verify_session4.EVIDENCE_ROOT", destination
    )

    published = _publish_evidence(staging, {"passed": False})

    assert published is False
    assert staging.is_dir()
    assert not destination.exists()


def test_passed_session4_validation_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "published"
    staging.mkdir()
    monkeypatch.setattr(
        "agent_service.scripts.verify_session4.EVIDENCE_ROOT", destination
    )

    published = _publish_evidence(staging, {"passed": True})

    assert published is True
    assert destination.is_dir()
    assert not staging.exists()


def test_session4_evidence_scan_accepts_redacted_markers(tmp_path: Path) -> None:
    evidence = tmp_path / "deployment-config.redacted.txt"
    evidence.write_text(
        "CHAT_API_KEY=<redacted>\nPILOT_API_KEY=<ephemeral>\n",
        encoding="utf-8",
    )

    _scan_evidence(tmp_path, ["real-provider-secret", "real-pilot-secret"])


@pytest.mark.parametrize(
    "leaked_value",
    [
        "Authorization: Bearer hidden-token",
        "data:image/png;base64,AAAA",
        "shortkey",
        "real-provider-secret",
        "real-pilot-secret",
    ],
)
def test_session4_evidence_scan_rejects_sensitive_values(
    tmp_path: Path, leaked_value: str
) -> None:
    (tmp_path / "evidence.json").write_text(leaked_value, encoding="utf-8")

    with pytest.raises(RuntimeError, match="脱敏扫描失败"):
        _scan_evidence(
            tmp_path,
            ["shortkey", "real-provider-secret", "real-pilot-secret"],
        )
