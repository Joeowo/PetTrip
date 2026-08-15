"""编排器前置验收：SQLite 缺失必须失败停止，不得静默初始化。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from run_unity_session4 import verify_existing_database


def test_missing_database_fails_instead_of_initializing(tmp_path: Path) -> None:
    """规格：数据库缺失时申请用户提供并停止——函数必须返回 None 且不创建文件。"""
    absent = tmp_path / "content-service.sqlite3"
    assert verify_existing_database(absent) is None
    assert not absent.exists(), "缺失分支不得创建数据库文件"


def test_unqueryable_database_fails(tmp_path: Path) -> None:
    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"this is not a database")
    assert verify_existing_database(broken) is None


def test_existing_schema_verified(tmp_path: Path) -> None:
    """既有库（含表结构）验证通过并返回行数统计。"""
    db = tmp_path / "content-service.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE validation_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL, screenshot_filename TEXT NOT NULL,
                screenshot_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL);
            INSERT INTO job_events (run_id, event, detail, created_at)
                VALUES ('r1', 'job.accepted', '{}', '2026-08-15T00:00:00+00:00');
            """
        )
    state = verify_existing_database(db)
    assert state is not None
    assert state["status"] == "existing-verified"
    assert state["job_events_rows"] == 1
    assert state["validation_reports_rows"] == 0
