"""目的地生成数据模型与持久化（T1 - issue #13）。

扩展现有 pilot4mvp2 存储基座，新增 11 张表支持目的地生成流程：
- 澄清阶段：clarification_inputs, clarification_state
- 目的地设计：destinations, destination_requirements, destination_requirement_items
- 场景规格：destination_specs, scene_plans
- 制品管理：shared_environment_artifacts, scene_artifacts, interaction_zones
- 操作记录：prompt_snapshots, operation_attempts

所有新表通过外键关联到现有 pilot4mvp2 基座（api_clients, sessions, runs, files）。
"""

from __future__ import annotations

# Schema 定义：11 张新表
DESTINATION_SCHEMA = """
-- ============================================================================
-- 澄清阶段表
-- ============================================================================

-- 澄清输入记录：每次玩家输入
CREATE TABLE IF NOT EXISTS clarification_inputs (
    input_id          TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    run_id            TEXT NOT NULL REFERENCES runs(id),
    raw_text          TEXT NOT NULL,
    classification    TEXT NOT NULL CHECK (
        classification IN ('empty', 'accepted_wish_input', 'off_topic', 'unintelligible')
    ),
    normalized_text   TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE (session_id, input_id)
);

-- 澄清状态：每个 session 一条
CREATE TABLE IF NOT EXISTS clarification_state (
    session_id            TEXT PRIMARY KEY REFERENCES sessions(id),
    clarification_closed  INTEGER NOT NULL DEFAULT 0 CHECK (clarification_closed IN (0, 1)),
    close_reason          TEXT CHECK (
        close_reason IS NULL OR
        close_reason IN ('accepted_wish_limit', 'non_accepted_limit', 'unity_requested')
    ),
    accepted_wish_count   INTEGER NOT NULL DEFAULT 0 CHECK (accepted_wish_count BETWEEN 0 AND 3),
    non_accepted_count    INTEGER NOT NULL DEFAULT 0 CHECK (non_accepted_count BETWEEN 0 AND 5),
    destination_id        TEXT REFERENCES destinations(id),
    closed_at             TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

-- ============================================================================
-- 目的地主表
-- ============================================================================

CREATE TABLE IF NOT EXISTS destinations (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    api_client_id     TEXT NOT NULL REFERENCES api_clients(id),
    phase             TEXT NOT NULL DEFAULT 'clarification' CHECK (
        phase IN ('clarification', 'requirements', 'specification', 'planning',
                  'shared_environment', 'scene_generation', 'terminal')
    ),
    done              INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    terminal_outcome  TEXT CHECK (
        terminal_outcome IS NULL OR
        terminal_outcome IN ('succeeded', 'partial_scene_failure', 'failed', 'fallback_selected')
    ),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- ============================================================================
-- 目的地要求（Requirements）
-- ============================================================================

-- 目的地要求集：冻结后不可变
CREATE TABLE IF NOT EXISTS destination_requirements (
    requirements_id   TEXT PRIMARY KEY,
    destination_id    TEXT NOT NULL REFERENCES destinations(id),
    source_inputs     TEXT NOT NULL,  -- JSON array of {input_id, raw_text, classification}
    frozen_at         TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

-- 要求明细项
CREATE TABLE IF NOT EXISTS destination_requirement_items (
    requirement_id        TEXT PRIMARY KEY,
    requirements_id       TEXT NOT NULL REFERENCES destination_requirements(requirements_id),
    normalized_statement  TEXT NOT NULL,
    polarity              TEXT NOT NULL CHECK (polarity IN ('include', 'exclude')),
    fulfillment           TEXT NOT NULL CHECK (
        fulfillment IN ('must_satisfy', 'best_effort', 'creative_discretion')
    ),
    source_type           TEXT NOT NULL CHECK (
        source_type IN ('player_input', 'agent_inference', 'template_default')
    ),
    source_input_ids      TEXT NOT NULL,  -- JSON array of UUIDs
    rationale             TEXT,
    created_at            TEXT NOT NULL,
    CHECK (source_type != 'agent_inference' OR rationale IS NOT NULL)
);

-- ============================================================================
-- 目的地规格（Specification）
-- ============================================================================

-- 目的地规格：锁定后不可变
CREATE TABLE IF NOT EXISTS destination_specs (
    spec_id                   TEXT PRIMARY KEY,
    destination_id            TEXT NOT NULL REFERENCES destinations(id),
    spec_version              INTEGER NOT NULL DEFAULT 1,
    template_id               TEXT NOT NULL,
    template_version          TEXT NOT NULL,
    requirements_id           TEXT NOT NULL REFERENCES destination_requirements(requirements_id),
    requirements_sha256       TEXT NOT NULL,
    title                     TEXT NOT NULL,
    shared_environment_spec   TEXT NOT NULL,  -- JSON object
    locked_at                 TEXT NOT NULL,
    sha256                    TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    UNIQUE (destination_id, spec_version)
);

-- 场景计划：每个目的地恰好 2 个（order=0,1）
CREATE TABLE IF NOT EXISTS scene_plans (
    scene_id            TEXT PRIMARY KEY,
    destination_id      TEXT NOT NULL REFERENCES destinations(id),
    spec_id             TEXT NOT NULL REFERENCES destination_specs(spec_id),
    order_index         INTEGER NOT NULL CHECK (order_index IN (0, 1)),
    state_label         TEXT NOT NULL,
    pet_behavior        TEXT NOT NULL,
    pet_emotion         TEXT NOT NULL,
    semantic_anchor     TEXT NOT NULL,
    interaction_prompt  TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE (destination_id, order_index)
);

-- ============================================================================
-- 制品管理
-- ============================================================================

-- 共享环境制品：不可变，内部使用
CREATE TABLE IF NOT EXISTS shared_environment_artifacts (
    shared_environment_id  TEXT PRIMARY KEY,
    destination_id         TEXT NOT NULL REFERENCES destinations(id),
    source_run_id          TEXT NOT NULL REFERENCES runs(id),
    image_file_id          TEXT NOT NULL REFERENCES files(id),
    image_sha256           TEXT NOT NULL,
    width_px               INTEGER NOT NULL CHECK (width_px > 0),
    height_px              INTEGER NOT NULL CHECK (height_px > 0),
    prompt_snapshot_id     TEXT,  -- 延迟关联到 prompt_snapshots
    created_at             TEXT NOT NULL
);

-- 场景制品：不可变，对外交付
CREATE TABLE IF NOT EXISTS scene_artifacts (
    scene_artifact_id          TEXT PRIMARY KEY,
    scene_id                   TEXT NOT NULL REFERENCES scene_plans(scene_id),
    destination_id             TEXT NOT NULL REFERENCES destinations(id),
    artifact_version           INTEGER NOT NULL DEFAULT 1,
    render_file_id             TEXT NOT NULL REFERENCES files(id),
    render_mime_type           TEXT NOT NULL,
    render_width_px            INTEGER NOT NULL CHECK (render_width_px > 0),
    render_height_px           INTEGER NOT NULL CHECK (render_height_px > 0),
    render_sha256              TEXT NOT NULL,
    interaction_zone_id        TEXT REFERENCES interaction_zones(zone_id),
    shared_environment_sha256  TEXT NOT NULL,
    prompt_snapshot_id         TEXT,
    created_at                 TEXT NOT NULL,
    UNIQUE (scene_id, artifact_version)
);

-- 交互区域
CREATE TABLE IF NOT EXISTS interaction_zones (
    zone_id           TEXT PRIMARY KEY,
    coordinate_space  TEXT NOT NULL DEFAULT 'pixel_top_left' CHECK (
        coordinate_space IN ('pixel_top_left', 'pixel_bottom_left')
    ),
    canvas_width_px   INTEGER NOT NULL CHECK (canvas_width_px > 0),
    canvas_height_px  INTEGER NOT NULL CHECK (canvas_height_px > 0),
    shape             TEXT NOT NULL DEFAULT 'circle' CHECK (shape = 'circle'),
    center_x_px       INTEGER NOT NULL CHECK (center_x_px >= 0),
    center_y_px       INTEGER NOT NULL CHECK (center_y_px >= 0),
    radius_px         INTEGER NOT NULL CHECK (radius_px > 0),
    created_at        TEXT NOT NULL,
    CHECK (center_x_px + radius_px <= canvas_width_px),
    CHECK (center_y_px + radius_px <= canvas_height_px),
    CHECK (center_x_px - radius_px >= 0),
    CHECK (center_y_px - radius_px >= 0)
);

-- ============================================================================
-- 操作记录
-- ============================================================================

-- Prompt 快照：记录生成参数
CREATE TABLE IF NOT EXISTS prompt_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    destination_id  TEXT NOT NULL REFERENCES destinations(id),
    operation_type  TEXT NOT NULL,  -- 'shared_environment' | 'scene_render' | 'locator'
    prompt_text     TEXT NOT NULL,
    model_params    TEXT,  -- JSON object
    created_at      TEXT NOT NULL
);

-- 操作尝试记录
CREATE TABLE IF NOT EXISTS operation_attempts (
    attempt_id      TEXT PRIMARY KEY,
    destination_id  TEXT NOT NULL REFERENCES destinations(id),
    scene_id        TEXT REFERENCES scene_plans(scene_id),
    operation_type  TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL CHECK (attempt_number BETWEEN 0 AND 3),
    run_id          TEXT NOT NULL REFERENCES runs(id),
    status          TEXT NOT NULL CHECK (status IN ('started', 'succeeded', 'failed')),
    error_code      TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

-- ============================================================================
-- 索引优化
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_clarification_inputs_session
    ON clarification_inputs(session_id);

CREATE INDEX IF NOT EXISTS idx_destinations_session
    ON destinations(session_id);

CREATE INDEX IF NOT EXISTS idx_destination_requirements_destination
    ON destination_requirements(destination_id);

CREATE INDEX IF NOT EXISTS idx_destination_requirement_items_requirements
    ON destination_requirement_items(requirements_id);

CREATE INDEX IF NOT EXISTS idx_scene_plans_destination
    ON scene_plans(destination_id);

CREATE INDEX IF NOT EXISTS idx_shared_environment_artifacts_destination
    ON shared_environment_artifacts(destination_id);

CREATE INDEX IF NOT EXISTS idx_scene_artifacts_scene
    ON scene_artifacts(scene_id);

CREATE INDEX IF NOT EXISTS idx_scene_artifacts_destination
    ON scene_artifacts(destination_id);

CREATE INDEX IF NOT EXISTS idx_prompt_snapshots_destination
    ON prompt_snapshots(destination_id);

CREATE INDEX IF NOT EXISTS idx_operation_attempts_destination
    ON operation_attempts(destination_id);

CREATE INDEX IF NOT EXISTS idx_operation_attempts_scene
    ON operation_attempts(scene_id);
"""


# ============================================================================
# Repository 层
# ============================================================================

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..shared.ids import new_id


def _utcnow_iso() -> str:
    """返回 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """将 sqlite3.Row 转换为字典。"""
    return dict(row) if row is not None else None


class DestinationRepository:
    """目的地生成数据访问层。

    提供基础 CRUD 操作和事务支持，所有访问通过锁串行化。
    不可变对象（Requirements、Specs、Artifacts）通过应用层只读断言保护。
    """

    def __init__(self, db_path: str | Path) -> None:
        """初始化 Repository 并执行迁移。

        Args:
            db_path: SQLite 数据库文件路径（与 pilot4mvp2 Storage 共用）
        """
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._is_open = False

    def open(self) -> None:
        """打开数据库连接并执行迁移（幂等）。"""
        if self._is_open:
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # 自动提交；事务用显式 BEGIN/COMMIT
        )
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._migrate()
        self._is_open = True

    def _configure(self) -> None:
        """配置 SQLite 连接参数。"""
        with self._lock:
            assert self._conn is not None
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA foreign_keys = ON")

    def _migrate(self) -> None:
        """在事务中执行建表迁移，不破坏现有数据。"""
        with self._lock:
            assert self._conn is not None
            # executescript() 会自动处理事务，不需要手动 BEGIN/COMMIT
            # 它会在执行前先 COMMIT 任何活动事务，然后在执行完后自动 COMMIT
            try:
                self._conn.executescript(DESTINATION_SCHEMA)
            except Exception:
                # executescript 失败时已自动回滚
                raise

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._is_open = False

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """显式事务上下文，异常自动回滚。

        Yields:
            sqlite3.Connection: 数据库连接对象

        Example:
            with repo.transaction() as conn:
                conn.execute("INSERT INTO destinations ...")
                conn.execute("INSERT INTO destination_specs ...")
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开，请先调用 open()")

        with self._lock:
            assert self._conn is not None
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ========================================================================
    # Destination CRUD
    # ========================================================================

    def create_destination(
        self,
        *,
        session_id: str,
        api_client_id: str,
    ) -> dict[str, Any]:
        """创建新目的地记录。

        Args:
            session_id: 会话 ID（必须存在于 sessions 表）
            api_client_id: API 客户端 ID（必须存在于 api_clients 表）

        Returns:
            dict: 新创建的目的地记录
        """
        destination_id = new_id("destination")
        now = _utcnow_iso()

        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO destinations(id, session_id, api_client_id, phase, done, "
                "terminal_outcome, created_at, updated_at) "
                "VALUES(?, ?, ?, 'clarification', 0, NULL, ?, ?)",
                (destination_id, session_id, api_client_id, now, now),
            )
            row = conn.execute(
                "SELECT * FROM destinations WHERE id = ?", (destination_id,)
            ).fetchone()

        return dict(row)

    def get_destination(self, destination_id: str) -> dict[str, Any] | None:
        """根据 ID 获取目的地记录。

        Args:
            destination_id: 目的地 ID

        Returns:
            dict | None: 目的地记录，不存在则返回 None
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM destinations WHERE id = ?", (destination_id,)
            ).fetchone()

        return _row_to_dict(row)

    def update_destination_phase(
        self,
        destination_id: str,
        phase: str,
    ) -> None:
        """更新目的地阶段。

        Args:
            destination_id: 目的地 ID
            phase: 新阶段（必须是有效的枚举值）
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        now = _utcnow_iso()
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "UPDATE destinations SET phase = ?, updated_at = ? WHERE id = ?",
                (phase, now, destination_id),
            )

    # ========================================================================
    # ClarificationState CRUD
    # ========================================================================

    def upsert_clarification_state(
        self,
        *,
        session_id: str,
        clarification_closed: bool = False,
        close_reason: str | None = None,
        accepted_wish_count: int = 0,
        non_accepted_count: int = 0,
        destination_id: str | None = None,
        closed_at: str | None = None,
    ) -> dict[str, Any]:
        """创建或更新澄清状态（幂等）。

        Args:
            session_id: 会话 ID
            clarification_closed: 是否已关闭
            close_reason: 关闭原因
            accepted_wish_count: 已接受愿望计数
            non_accepted_count: 未接受计数
            destination_id: 关联的目的地 ID
            closed_at: 关闭时间

        Returns:
            dict: 更新后的澄清状态记录
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        now = _utcnow_iso()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM clarification_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            if existing is None:
                # 创建新记录
                conn.execute(
                    "INSERT INTO clarification_state(session_id, clarification_closed, "
                    "close_reason, accepted_wish_count, non_accepted_count, destination_id, "
                    "closed_at, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        1 if clarification_closed else 0,
                        close_reason,
                        accepted_wish_count,
                        non_accepted_count,
                        destination_id,
                        closed_at,
                        now,
                        now,
                    ),
                )
            else:
                # 更新现有记录
                conn.execute(
                    "UPDATE clarification_state SET clarification_closed = ?, "
                    "close_reason = ?, accepted_wish_count = ?, non_accepted_count = ?, "
                    "destination_id = ?, closed_at = ?, updated_at = ? WHERE session_id = ?",
                    (
                        1 if clarification_closed else 0,
                        close_reason,
                        accepted_wish_count,
                        non_accepted_count,
                        destination_id,
                        closed_at,
                        now,
                        session_id,
                    ),
                )

            row = conn.execute(
                "SELECT * FROM clarification_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        return dict(row)

    def get_clarification_state(self, session_id: str) -> dict[str, Any] | None:
        """获取澄清状态。

        Args:
            session_id: 会话 ID

        Returns:
            dict | None: 澄清状态记录，不存在则返回 None
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM clarification_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        return _row_to_dict(row)

    # ========================================================================
    # ClarificationInput CRUD
    # ========================================================================

    def create_clarification_input(
        self,
        *,
        session_id: str,
        run_id: str,
        raw_text: str,
        classification: str,
        normalized_text: str | None = None,
    ) -> dict[str, Any]:
        """创建澄清输入记录。

        Args:
            session_id: 会话 ID
            run_id: Run ID
            raw_text: 原始文本
            classification: 分类（empty/accepted_wish_input/off_topic/unintelligible）
            normalized_text: 标准化文本

        Returns:
            dict: 新创建的输入记录
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        input_id = new_id("input")
        now = _utcnow_iso()

        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO clarification_inputs(input_id, session_id, run_id, raw_text, "
                "classification, normalized_text, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (input_id, session_id, run_id, raw_text, classification, normalized_text, now),
            )
            row = conn.execute(
                "SELECT * FROM clarification_inputs WHERE input_id = ?",
                (input_id,),
            ).fetchone()

        return dict(row)

    def list_clarification_inputs(self, session_id: str) -> list[dict[str, Any]]:
        """列出会话的所有澄清输入。

        Args:
            session_id: 会话 ID

        Returns:
            list[dict]: 输入记录列表（按创建时间排序）
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT * FROM clarification_inputs WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()

        return [dict(row) for row in rows]
