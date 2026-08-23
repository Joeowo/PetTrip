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
-- 澄清阶段表（注意：这些表已在 database.py 中定义，这里不重复创建）
-- ============================================================================

-- clarification_inputs 和 clarification_sessions 已在 database.py 中定义
-- 这里只添加新的目的地相关表

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
-- 模板设计（Issue #36 - 2.3）
-- ============================================================================

-- 环境模板设计：存储画风和构图模板选择结果
CREATE TABLE IF NOT EXISTS environment_template_designs (
    design_id             TEXT PRIMARY KEY,
    destination_id        TEXT NOT NULL REFERENCES destinations(id),
    requirements_id       TEXT NOT NULL REFERENCES destination_requirements(requirements_id),
    style_template_id     TEXT NOT NULL,
    composition_template_id TEXT NOT NULL,
    rationale             TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE (destination_id)  -- 每个目的地只有一个模板设计
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

CREATE INDEX IF NOT EXISTS idx_destinations_session
    ON destinations(session_id);

CREATE INDEX IF NOT EXISTS idx_destination_requirements_destination
    ON destination_requirements(destination_id);

CREATE INDEX IF NOT EXISTS idx_destination_requirement_items_requirements
    ON destination_requirement_items(requirements_id);

CREATE INDEX IF NOT EXISTS idx_environment_template_designs_destination
    ON environment_template_designs(destination_id);

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
        """在事务中执行建表迁移，不破坏现有数据。

        Bug 1.2 修复：不使用 executescript()，因为它会自动 COMMIT 活动事务。
        改为在显式事务中逐条执行 SQL 语句。
        """
        with self._lock:
            assert self._conn is not None
            # 拆分 schema 为单独的语句
            # 使用简单但可靠的方法：按分号分割，然后清理注释
            raw_statements = DESTINATION_SCHEMA.split(";")
            statements = []

            for stmt in raw_statements:
                # 移除注释行并清理空白
                lines = []
                for line in stmt.split("\n"):
                    stripped = line.strip()
                    # 跳过纯注释行
                    if stripped and not stripped.startswith("--"):
                        lines.append(line)

                cleaned_stmt = "\n".join(lines).strip()
                if cleaned_stmt:
                    statements.append(cleaned_stmt)

            # 在显式事务中逐条执行
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                for stmt in statements:
                    self._conn.execute(stmt)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
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
    # ClarificationState CRUD（使用 database.py 中的 clarification_sessions 表）
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
                "SELECT * FROM clarification_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            if existing is None:
                # 创建新记录
                conn.execute(
                    "INSERT INTO clarification_sessions(session_id, clarification_closed, "
                    "accepted_wish_count, non_accepted_count, close_reason, destination_id, "
                    "closed_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        1 if clarification_closed else 0,
                        accepted_wish_count,
                        non_accepted_count,
                        close_reason,
                        destination_id,
                        closed_at,
                    ),
                )
            else:
                # 更新现有记录
                conn.execute(
                    "UPDATE clarification_sessions SET clarification_closed = ?, "
                    "close_reason = ?, accepted_wish_count = ?, non_accepted_count = ?, "
                    "destination_id = ?, closed_at = ? WHERE session_id = ?",
                    (
                        1 if clarification_closed else 0,
                        close_reason,
                        accepted_wish_count,
                        non_accepted_count,
                        destination_id,
                        closed_at,
                        session_id,
                    ),
                )

            row = conn.execute(
                "SELECT * FROM clarification_sessions WHERE session_id = ?",
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
                "SELECT * FROM clarification_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        return _row_to_dict(row)

    # ========================================================================
    # ClarificationInput CRUD（使用 database.py 中的 clarification_inputs 表）
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

        # 使用 database.py 中的表结构：id 作为主键，input_id 作为业务 ID
        record_id = new_id("clarification_input")
        input_id = new_id("input")
        now = _utcnow_iso()

        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO clarification_inputs(id, session_id, run_id, input_id, raw_text, "
                "classification, normalized_text, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (record_id, session_id, run_id, input_id, raw_text, classification, normalized_text, now),
            )
            row = conn.execute(
                "SELECT * FROM clarification_inputs WHERE id = ?",
                (record_id,),
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

    # ========================================================================
    # DestinationRequirements CRUD
    # ========================================================================

    def create_destination_requirements(
        self,
        *,
        destination_id: str,
        source_inputs: list[dict[str, Any]],
        sha256: str,
    ) -> dict[str, Any]:
        """创建目的地要求集（冻结后不可变）。

        Args:
            destination_id: 目的地 ID
            source_inputs: 源输入列表 [{input_id, raw_text, classification}]
            sha256: 要求集的 SHA-256 哈希

        Returns:
            dict: 新创建的 Requirements 记录
        """
        import json

        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        requirements_id = new_id("requirements")
        now = _utcnow_iso()

        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO destination_requirements(requirements_id, destination_id, "
                "source_inputs, frozen_at, sha256, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    requirements_id,
                    destination_id,
                    json.dumps(source_inputs),
                    now,
                    sha256,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM destination_requirements WHERE requirements_id = ?",
                (requirements_id,),
            ).fetchone()

        return dict(row)

    def create_requirement_item(
        self,
        *,
        requirements_id: str,
        normalized_statement: str,
        polarity: str,
        fulfillment: str,
        source_type: str,
        source_input_ids: list[str],
        rationale: str | None = None,
    ) -> dict[str, Any]:
        """创建要求明细项。

        Args:
            requirements_id: 要求集 ID
            normalized_statement: 标准化陈述
            polarity: 极性（include/exclude）
            fulfillment: 执行度（must_satisfy/best_effort/creative_discretion）
            source_type: 来源类型（player_input/agent_inference/template_default）
            source_input_ids: 源输入 ID 列表
            rationale: 依据（source_type=agent_inference 时必须）

        Returns:
            dict: 新创建的要求项记录
        """
        import json

        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        # 校验不变量：agent_inference 必须有 rationale
        if source_type == "agent_inference" and not rationale:
            raise ValueError("source_type=agent_inference 时 rationale 不能为空")

        requirement_id = new_id("requirement")
        now = _utcnow_iso()

        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO destination_requirement_items(requirement_id, requirements_id, "
                "normalized_statement, polarity, fulfillment, source_type, source_input_ids, "
                "rationale, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    requirement_id,
                    requirements_id,
                    normalized_statement,
                    polarity,
                    fulfillment,
                    source_type,
                    json.dumps(source_input_ids),
                    rationale,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM destination_requirement_items WHERE requirement_id = ?",
                (requirement_id,),
            ).fetchone()

        return dict(row)

    def get_destination_requirements(
        self, destination_id: str
    ) -> dict[str, Any] | None:
        """获取目的地的 Requirements 记录。

        Args:
            destination_id: 目的地 ID

        Returns:
            dict | None: Requirements 记录，不存在则返回 None
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM destination_requirements WHERE destination_id = ?",
                (destination_id,),
            ).fetchone()

        return _row_to_dict(row)

    def list_requirement_items(self, requirements_id: str) -> list[dict[str, Any]]:
        """列出 Requirements 的所有明细项。

        Args:
            requirements_id: 要求集 ID

        Returns:
            list[dict]: 要求项列表（按创建时间排序）
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT * FROM destination_requirement_items WHERE requirements_id = ? "
                "ORDER BY created_at ASC",
                (requirements_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    # ========================================================================
    # EnvironmentTemplateDesign CRUD（Issue #36 - 2.3）
    # ========================================================================

    def create_environment_template_design(
        self,
        *,
        destination_id: str,
        requirements_id: str,
        style_template_id: str,
        composition_template_id: str,
        rationale: str,
    ) -> dict[str, Any]:
        """创建环境模板设计记录。

        Args:
            destination_id: 目的地 ID
            requirements_id: 要求集 ID
            style_template_id: 画风模板 ID
            composition_template_id: 构图模板 ID
            rationale: 选择推理

        Returns:
            dict: 新创建的模板设计记录
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        design_id = new_id("template_design")
        now = _utcnow_iso()

        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO environment_template_designs(design_id, destination_id, "
                "requirements_id, style_template_id, composition_template_id, rationale, "
                "created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    design_id,
                    destination_id,
                    requirements_id,
                    style_template_id,
                    composition_template_id,
                    rationale,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM environment_template_designs WHERE design_id = ?",
                (design_id,),
            ).fetchone()

        return dict(row)

    def get_environment_template_design(
        self, destination_id: str
    ) -> dict[str, Any] | None:
        """获取目的地的环境模板设计。

        Args:
            destination_id: 目的地 ID

        Returns:
            dict | None: 模板设计记录，不存在则返回 None
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM environment_template_designs WHERE destination_id = ?",
                (destination_id,),
            ).fetchone()

        return _row_to_dict(row)

    # ========================================================================
    # DestinationSpec CRUD
    # ========================================================================

    def create_destination_spec(
        self,
        *,
        destination_id: str,
        spec_version: int,
        template_id: str,
        template_version: str,
        requirements_id: str,
        requirements_sha256: str,
        title: str,
        shared_environment_spec: dict[str, Any],
        sha256: str,
    ) -> dict[str, Any]:
        """创建目的地规格（锁定后不可变）。

        Args:
            destination_id: 目的地 ID
            spec_version: 规格版本（首阶段固定为 1）
            template_id: 模板 ID
            template_version: 模板版本
            requirements_id: 要求集 ID
            requirements_sha256: 要求集 SHA-256
            title: 标题
            shared_environment_spec: 共享环境规格（JSON 对象）
            sha256: 规格的 SHA-256 哈希

        Returns:
            dict: 新创建的 Spec 记录
        """
        import json

        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        spec_id = new_id("spec")
        now = _utcnow_iso()

        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO destination_specs(spec_id, destination_id, spec_version, "
                "template_id, template_version, requirements_id, requirements_sha256, "
                "title, shared_environment_spec, locked_at, sha256, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec_id,
                    destination_id,
                    spec_version,
                    template_id,
                    template_version,
                    requirements_id,
                    requirements_sha256,
                    title,
                    json.dumps(shared_environment_spec),
                    now,
                    sha256,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM destination_specs WHERE spec_id = ?",
                (spec_id,),
            ).fetchone()

        return dict(row)

    def get_destination_spec(self, destination_id: str) -> dict[str, Any] | None:
        """获取目的地的 Spec 记录。

        Args:
            destination_id: 目的地 ID

        Returns:
            dict | None: Spec 记录，不存在则返回 None
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT * FROM destination_specs WHERE destination_id = ? "
                "ORDER BY spec_version DESC LIMIT 1",
                (destination_id,),
            ).fetchone()

        return _row_to_dict(row)

    # ========================================================================
    # ScenePlan CRUD
    # ========================================================================

    def create_scene_plan(
        self,
        *,
        destination_id: str,
        spec_id: str,
        order_index: int,
        state_label: str,
        pet_behavior: str,
        pet_emotion: str,
        semantic_anchor: str,
        interaction_prompt: str,
    ) -> dict[str, Any]:
        """创建场景计划。

        Args:
            destination_id: 目的地 ID
            spec_id: 规格 ID
            order_index: 顺序索引（0 或 1）
            state_label: 状态标签
            pet_behavior: 宠物行为
            pet_emotion: 宠物情绪
            semantic_anchor: 语义锚点
            interaction_prompt: 交互提示

        Returns:
            dict: 新创建的 ScenePlan 记录
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        scene_id = new_id("scene")
        now = _utcnow_iso()

        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO scene_plans(scene_id, destination_id, spec_id, order_index, "
                "state_label, pet_behavior, pet_emotion, semantic_anchor, "
                "interaction_prompt, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scene_id,
                    destination_id,
                    spec_id,
                    order_index,
                    state_label,
                    pet_behavior,
                    pet_emotion,
                    semantic_anchor,
                    interaction_prompt,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM scene_plans WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()

        return dict(row)

    def list_scene_plans(self, destination_id: str) -> list[dict[str, Any]]:
        """列出目的地的所有场景计划。

        Args:
            destination_id: 目的地 ID

        Returns:
            list[dict]: 场景计划列表（按 order_index 排序）
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT * FROM scene_plans WHERE destination_id = ? ORDER BY order_index ASC",
                (destination_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    # ========================================================================
    # 协调器支持方法（Bug 1.1 修复 - ADR 0002 封装）
    # ========================================================================

    def list_destinations(self, status: str | None = None) -> list[dict[str, Any]]:
        """列出所有目的地记录。

        Args:
            status: 可选过滤条件
                - None: 返回所有目的地
                - 'pending': 返回非终态目的地（done=0）
                - 'done': 返回已完成目的地（done=1）

        Returns:
            list[dict]: 目的地记录列表（按创建时间排序）
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            if status == "pending":
                rows = self._conn.execute(
                    "SELECT * FROM destinations WHERE done = 0 ORDER BY created_at ASC"
                ).fetchall()
            elif status == "done":
                rows = self._conn.execute(
                    "SELECT * FROM destinations WHERE done = 1 ORDER BY created_at ASC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM destinations ORDER BY created_at ASC"
                ).fetchall()

        return [dict(row) for row in rows]

    def has_frozen_requirements(self, destination_id: str) -> bool:
        """检查目的地是否有已冻结的 Requirements。

        Args:
            destination_id: 目的地 ID

        Returns:
            bool: 是否存在已冻结的 Requirements 记录
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT 1 FROM destination_requirements WHERE destination_id = ? LIMIT 1",
                (destination_id,),
            ).fetchone()

        return row is not None

    def has_locked_spec(self, destination_id: str) -> bool:
        """检查目的地是否有已锁定的 Spec。

        Args:
            destination_id: 目的地 ID

        Returns:
            bool: 是否存在已锁定的 Spec 记录
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT 1 FROM destination_specs WHERE destination_id = ? LIMIT 1",
                (destination_id,),
            ).fetchone()

        return row is not None

    def has_shared_environment(self, destination_id: str) -> bool:
        """检查目的地是否有已生成的 SharedEnvironment。

        Args:
            destination_id: 目的地 ID

        Returns:
            bool: 是否存在 SharedEnvironment 制品记录
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT 1 FROM shared_environment_artifacts WHERE destination_id = ? LIMIT 1",
                (destination_id,),
            ).fetchone()

        return row is not None

    def get_scene_status(self, destination_id: str) -> dict[str, Any]:
        """获取目的地的场景状态统计。

        Args:
            destination_id: 目的地 ID

        Returns:
            dict: 场景状态统计
                - total_scenes: 总场景数
                - ready_scenes: 已完成场景数
                - failed_scenes: 失败场景数
                - all_ready: 是否全部完成
                - all_failed: 是否全部失败
        """
        if not self._is_open:
            raise RuntimeError("Repository 未打开")

        with self._lock:
            assert self._conn is not None
            # 获取所有场景计划
            scene_plans = self._conn.execute(
                "SELECT scene_id FROM scene_plans WHERE destination_id = ? ORDER BY order_index ASC",
                (destination_id,),
            ).fetchall()

            total_scenes = len(scene_plans)
            ready_scenes = 0
            failed_scenes = 0

            # 检查每个场景的 artifact 状态
            for plan in scene_plans:
                scene_id = plan["scene_id"]
                artifact = self._conn.execute(
                    "SELECT 1 FROM scene_artifacts WHERE scene_id = ? LIMIT 1",
                    (scene_id,),
                ).fetchone()

                if artifact is not None:
                    ready_scenes += 1
                else:
                    # 检查是否有失败的 attempt 记录
                    failed_attempt = self._conn.execute(
                        "SELECT 1 FROM operation_attempts "
                        "WHERE scene_id = ? AND status = 'failed' "
                        "AND attempt_number >= 2 "  # 最多 3 次尝试（0, 1, 2）
                        "LIMIT 1",
                        (scene_id,),
                    ).fetchone()

                    if failed_attempt is not None:
                        failed_scenes += 1

        return {
            "total_scenes": total_scenes,
            "ready_scenes": ready_scenes,
            "failed_scenes": failed_scenes,
            "all_ready": total_scenes > 0 and ready_scenes == total_scenes,
            "all_failed": total_scenes > 0 and failed_scenes == total_scenes,
        }
