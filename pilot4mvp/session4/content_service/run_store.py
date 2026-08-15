"""会话4 run 目录管理与 SQLite 持久化（job 事件与 Unity 验证报告）。

- run 目录：runs/<run_id>/，统一输入时从源 run 复制上游 artifact（world-spec、
  scene-plan、assets/），此后所有落盘都在该目录内。
- SQLite：job_events（job.accepted / job.replayed）与 validation_reports 两张表，
  标准库 sqlite3，无 ORM。
- 事件同时落盘 run 目录 events.jsonl，SQLite 是查询入口，jsonl 是目录内证据。
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from .snapshot_builder import sha256_bytes, sha256_file

RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ARTIFACT_NAMES = ("world-spec.json", "scene-plan.json")
CONTENT_READY_NAME = "content-ready.json"
ACTIVE_SNAPSHOT_NAME = "active-snapshot.txt"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RunStoreError(Exception):
    """面向调用方的可预期错误，服务层映射为 4xx。"""


class RunStore:
    def __init__(self, state_dir: Path, db_path: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ---------- SQLite ----------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_job_events_run ON job_events(run_id);
                CREATE TABLE IF NOT EXISTS validation_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    screenshot_filename TEXT NOT NULL,
                    screenshot_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reports_run ON validation_reports(run_id);
                """
            )

    def append_event(self, run_id: str, event: str, detail: dict) -> None:
        created_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO job_events (run_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
                (run_id, event, json.dumps(detail, ensure_ascii=False), created_at),
            )
        record = {"run_id": run_id, "event": event, "detail": detail, "created_at": created_at}
        events_file = self.run_dir(run_id) / "events.jsonl"
        with events_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load_events(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event, detail, created_at FROM job_events WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [
            {"event": row["event"], "detail": json.loads(row["detail"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def load_reports(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, snapshot_sha256, screenshot_filename, screenshot_sha256, created_at"
                " FROM validation_reports WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_report(self, run_id: str, payload: dict, screenshot_png: bytes) -> int:
        """截图重新打开校验后落盘 run 目录并写入 SQLite，返回报告 ID。"""
        try:
            with Image.open(BytesIO(screenshot_png)) as image:
                image.load()
                if image.format != "PNG":
                    raise RunStoreError("screenshot is not PNG")
                width, height = image.size
        except RunStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RunStoreError("screenshot cannot be decoded: " + str(exc)) from exc

        screenshot_dir = self.run_dir(run_id) / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        digest = sha256_bytes(screenshot_png)
        filename = f"unity-validation-{digest[:12]}.png"
        (screenshot_dir / filename).write_bytes(screenshot_png)

        stored = dict(payload)
        stored["screenshot"] = {
            "filename": f"screenshots/{filename}",
            "sha256": digest,
            "width": width,
            "height": height,
        }
        report_path = self.run_dir(run_id) / "unity-report.json"
        report_path.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO validation_reports"
                " (run_id, snapshot_sha256, screenshot_filename, screenshot_sha256, payload_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    payload["snapshot_sha256"],
                    f"screenshots/{filename}",
                    digest,
                    json.dumps(stored, ensure_ascii=False),
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    # ---------- run 目录 ----------

    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID_PATTERN.match(run_id):
            raise RunStoreError("invalid run_id: " + run_id)
        return self.state_dir / run_id

    def has_run(self, run_id: str) -> bool:
        try:
            return self.run_dir(run_id).is_dir()
        except RunStoreError:
            return False

    def create_run(self, run_id: str, input_text: str, source_run_dir: Path) -> Path:
        """从源 run 复制上游 artifact 创建新 run，写 input.json 与 job.accepted。"""
        if not RUN_ID_PATTERN.match(run_id):
            raise RunStoreError("invalid run_id: " + run_id)
        target = self.state_dir / run_id
        if target.exists():
            raise RunStoreError("run_id already exists: " + run_id)
        if not source_run_dir.is_dir():
            raise RunStoreError("source run directory does not exist: " + source_run_dir.name)
        for name in ARTIFACT_NAMES:
            if not (source_run_dir / name).is_file():
                raise RunStoreError("source run is missing artifact: " + name)
        if not (source_run_dir / "assets").is_dir():
            raise RunStoreError("source run is missing assets directory")

        target.mkdir(parents=True)
        for name in ARTIFACT_NAMES:
            shutil.copy2(source_run_dir / name, target / name)
        shutil.copytree(source_run_dir / "assets", target / "assets")
        (target / "input.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "input": input_text,
                    "source_run_id": source_run_dir.name,
                    "model_calls": "none (session4 replay-based run)",
                    "created_at": utc_now(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.append_event(
            run_id,
            "job.accepted",
            {"source_run_id": source_run_dir.name, "input_length": len(input_text)},
        )
        return target

    def mark_content_ready(self, run_id: str, snapshot_name: str) -> None:
        run = self.run_dir(run_id)
        (run / CONTENT_READY_NAME).write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "created_at": utc_now(),
                    "scene_snapshot": snapshot_name,
                    "asset_manifest": "asset-manifest.json",
                    "unity": "not_tested",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (run / ACTIVE_SNAPSHOT_NAME).write_text(snapshot_name + "\n", encoding="utf-8")

    def active_snapshot_name(self, run_id: str) -> str:
        marker = self.run_dir(run_id) / ACTIVE_SNAPSHOT_NAME
        if not marker.is_file():
            raise RunStoreError("run is not content-ready: " + run_id)
        return marker.read_text(encoding="utf-8").strip()

    def require_content_ready(self, run_id: str) -> Path:
        run = self.run_dir(run_id)
        if not run.is_dir():
            raise RunStoreError("run directory does not exist: " + run_id)
        if not (run / CONTENT_READY_NAME).is_file():
            raise RunStoreError("run directory is not content-ready: " + run_id)
        return run

    def snapshot_sha256(self, run_id: str) -> str:
        run = self.require_content_ready(run_id)
        return sha256_file(run / self.active_snapshot_name(run_id))


def decode_base64_png(encoded: str) -> bytes:
    try:
        payload = base64.b64decode(encoded, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise RunStoreError("screenshot base64 is invalid") from exc
    if not payload.startswith(b"\x89PNG"):
        raise RunStoreError("decoded screenshot is not a PNG payload")
    return payload
