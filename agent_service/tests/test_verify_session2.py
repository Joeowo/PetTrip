from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_service.scripts.verify_session2 import (
    _redact_server_log,
    _sqlite_has_no_binary,
)


def test_sqlite_check_rejects_blob_and_base64_in_any_table(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE sample(value)")
    connection.execute("INSERT INTO sample(value) VALUES('plain text')")
    connection.commit()
    connection.close()
    assert _sqlite_has_no_binary(db_path) is True

    connection = sqlite3.connect(db_path)
    connection.execute("INSERT INTO sample(value) VALUES(?)", (b"binary",))
    connection.commit()
    connection.close()
    assert _sqlite_has_no_binary(db_path) is False

    connection = sqlite3.connect(db_path)
    connection.execute("DELETE FROM sample")
    connection.execute(
        "INSERT INTO sample(value) VALUES(?)",
        ("data:image/png;base64,AAAA",),
    )
    connection.commit()
    connection.close()
    assert _sqlite_has_no_binary(db_path) is False


def test_server_log_redacts_paths_and_tracebacks(tmp_path: Path) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text(
        "error at C:\\private\\agent.db\n"
        "Traceback (most recent call last):\n"
        "  File \"/private/service.py\", line 1\n"
        "ValueError: failed\n"
        "request_completed request_id=req_123 status=500\n",
        encoding="utf-8",
    )

    _redact_server_log(log_path)
    redacted = log_path.read_text(encoding="utf-8")
    assert "C:\\private" not in redacted
    assert "/private/" not in redacted
    assert "Traceback" not in redacted
    assert "<redacted-path>" in redacted
    assert "<redacted-traceback>" in redacted
