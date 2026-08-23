"""T1 测试用例：数据模型与持久化基座（issue #13）。

测试要求（6 项必过）：
1. 启动时建表成功，不破坏现有数据
2. 所有外键约束生效
3. UUID 主键正常生成
4. 基础插入与查询可执行
5. 事务回滚时数据不提交
6. 唯一约束冲突时抛出可识别错误
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from agent_service.storage.destination_storage import DestinationRepository
from agent_service.shared.ids import new_id
from agent_service.storage import Storage


# ============================================================================
# Test 1: 启动时建表成功，不破坏现有数据
# ============================================================================


def test_migration_creates_tables_without_breaking_existing_data():
    """测试：启动时建表成功，不破坏现有 pilot4mvp2 数据。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        # 1. 先用现有 Storage 创建基础表并插入数据
        storage = Storage(db_path, recover=False)
        client_id = storage.upsert_api_client("test_hash", "test_client")
        session = storage.create_session(client_id)
        storage.close()

        # 2. 用 DestinationRepository 打开并迁移
        repo = DestinationRepository(db_path)
        repo.open()

        # 3. 验证新表已创建
        with repo._lock:
            assert repo._conn is not None
            tables = repo._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = {row["name"] for row in tables}

        # 验证新增的 11 张表都存在
        expected_new_tables = {
            "clarification_inputs",
            "clarification_state",
            "destinations",
            "destination_requirements",
            "destination_requirement_items",
            "destination_specs",
            "scene_plans",
            "shared_environment_artifacts",
            "scene_artifacts",
            "interaction_zones",
            "prompt_snapshots",
            "operation_attempts",
        }
        assert expected_new_tables.issubset(table_names)

        # 验证旧表仍然存在
        expected_old_tables = {
            "api_clients",
            "sessions",
            "runs",
            "messages",
            "run_events",
            "files",
            "message_files",
        }
        assert expected_old_tables.issubset(table_names)

        # 4. 验证现有数据未被破坏
        storage2 = Storage(db_path, recover=False)
        retrieved_session = storage2.get_session(session["id"], client_id)
        assert retrieved_session is not None
        assert retrieved_session["id"] == session["id"]
        storage2.close()

        repo.close()


# ============================================================================
# Test 2: 所有外键约束生效
# ============================================================================


def test_foreign_key_constraints_are_enforced():
    """测试：所有外键约束生效。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        # 初始化存储和仓库
        storage = Storage(db_path, recover=False)
        repo = DestinationRepository(db_path)
        repo.open()

        # 测试：插入 destination 时引用不存在的 session_id 应失败
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            with repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO destinations(id, session_id, api_client_id, phase, "
                    "done, created_at, updated_at) VALUES(?, ?, ?, 'clarification', 0, "
                    "datetime('now'), datetime('now'))",
                    (new_id("destination"), "nonexistent_session", "nonexistent_client"),
                )

        # 测试：插入 clarification_input 时引用不存在的 run_id 应失败
        client_id = storage.upsert_api_client("test_hash", "test_client")
        session = storage.create_session(client_id)

        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            with repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO clarification_inputs(input_id, session_id, run_id, "
                    "raw_text, classification, created_at) VALUES(?, ?, ?, ?, ?, datetime('now'))",
                    (new_id("input"), session["id"], "nonexistent_run", "test", "empty"),
                )

        storage.close()
        repo.close()


# ============================================================================
# Test 3: UUID 主键正常生成
# ============================================================================


def test_uuid_primary_keys_are_generated():
    """测试：UUID 主键正常生成。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        storage = Storage(db_path, recover=False)
        repo = DestinationRepository(db_path)
        repo.open()

        # 创建基础数据
        client_id = storage.upsert_api_client("test_hash", "test_client")
        session = storage.create_session(client_id)

        # 创建 destination 并验证 ID 格式
        destination = repo.create_destination(
            session_id=session["id"],
            api_client_id=client_id,
        )

        assert destination["id"].startswith("destination_")
        assert len(destination["id"]) > len("destination_")

        # 创建多个记录，验证 ID 唯一性
        destinations = [
            repo.create_destination(session_id=session["id"], api_client_id=client_id)
            for _ in range(3)
        ]

        ids = [d["id"] for d in destinations]
        assert len(ids) == len(set(ids))  # 所有 ID 唯一

        storage.close()
        repo.close()


# ============================================================================
# Test 4: 基础插入与查询可执行
# ============================================================================


def test_basic_insert_and_query_operations():
    """测试：基础插入与查询可执行。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        storage = Storage(db_path, recover=False)
        repo = DestinationRepository(db_path)
        repo.open()

        # 创建基础数据
        client_id = storage.upsert_api_client("test_hash", "test_client")
        session = storage.create_session(client_id)

        # 测试 Destination CRUD
        destination = repo.create_destination(
            session_id=session["id"],
            api_client_id=client_id,
        )
        assert destination["phase"] == "clarification"
        assert destination["done"] == 0

        retrieved = repo.get_destination(destination["id"])
        assert retrieved is not None
        assert retrieved["id"] == destination["id"]

        repo.update_destination_phase(destination["id"], "requirements")
        updated = repo.get_destination(destination["id"])
        assert updated["phase"] == "requirements"

        # 测试 ClarificationState CRUD
        state = repo.upsert_clarification_state(
            session_id=session["id"],
            accepted_wish_count=1,
            destination_id=destination["id"],
        )
        assert state["accepted_wish_count"] == 1
        assert state["clarification_closed"] == 0

        retrieved_state = repo.get_clarification_state(session["id"])
        assert retrieved_state is not None
        assert retrieved_state["accepted_wish_count"] == 1

        # 测试 ClarificationInput CRUD
        # 先创建一个 run
        run = storage.create_run(
            api_client_id=client_id,
            session_id=session["id"],
            request_input={"text": "test input"},
            response_format={"type": "text"},
            idempotency_key="test_key_1",
            idempotency_body_hash="test_hash_1",
        )

        input_record = repo.create_clarification_input(
            session_id=session["id"],
            run_id=run["id"],
            raw_text="我想去海边",
            classification="accepted_wish_input",
            normalized_text="去海边",
        )
        assert input_record["raw_text"] == "我想去海边"
        assert input_record["classification"] == "accepted_wish_input"

        inputs = repo.list_clarification_inputs(session["id"])
        assert len(inputs) == 1
        assert inputs[0]["input_id"] == input_record["input_id"]

        storage.close()
        repo.close()


# ============================================================================
# Test 5: 事务回滚时数据不提交
# ============================================================================


def test_transaction_rollback_does_not_commit_data():
    """测试：事务回滚时数据不提交。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        storage = Storage(db_path, recover=False)
        repo = DestinationRepository(db_path)
        repo.open()

        client_id = storage.upsert_api_client("test_hash", "test_client")
        session = storage.create_session(client_id)

        # 尝试在事务中插入，然后触发异常回滚
        destination_id = new_id("destination")
        try:
            with repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO destinations(id, session_id, api_client_id, phase, "
                    "done, created_at, updated_at) VALUES(?, ?, ?, 'clarification', 0, "
                    "datetime('now'), datetime('now'))",
                    (destination_id, session["id"], client_id),
                )
                # 故意触发异常
                raise RuntimeError("Test rollback")
        except RuntimeError:
            pass

        # 验证数据未提交
        result = repo.get_destination(destination_id)
        assert result is None

        storage.close()
        repo.close()


# ============================================================================
# Test 6: 唯一约束冲突时抛出可识别错误
# ============================================================================


def test_unique_constraint_violations_raise_identifiable_errors():
    """测试：唯一约束冲突时抛出可识别错误。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        storage = Storage(db_path, recover=False)
        repo = DestinationRepository(db_path)
        repo.open()

        client_id = storage.upsert_api_client("test_hash", "test_client")
        session = storage.create_session(client_id)
        destination = repo.create_destination(
            session_id=session["id"],
            api_client_id=client_id,
        )

        # 测试：同一 destination 的 scene_plans 不能有重复的 order_index
        with repo.transaction() as conn:
            # 先插入 destination_spec（scene_plans 需要引用）
            spec_id = new_id("spec")
            requirements_id = new_id("requirements")

            # 先插入 requirements
            conn.execute(
                "INSERT INTO destination_requirements(requirements_id, destination_id, "
                "source_inputs, frozen_at, sha256, created_at) "
                "VALUES(?, ?, '[]', datetime('now'), 'test_sha', datetime('now'))",
                (requirements_id, destination["id"]),
            )

            conn.execute(
                "INSERT INTO destination_specs(spec_id, destination_id, spec_version, "
                "template_id, template_version, requirements_id, requirements_sha256, "
                "title, shared_environment_spec, locked_at, sha256, created_at) "
                "VALUES(?, ?, 1, 'template1', 'v1', ?, 'sha256', 'Test Spec', '{}', "
                "datetime('now'), 'spec_sha', datetime('now'))",
                (spec_id, destination["id"], requirements_id),
            )

            # 插入第一个 scene_plan (order_index=0)
            scene_id_1 = new_id("scene")
            conn.execute(
                "INSERT INTO scene_plans(scene_id, destination_id, spec_id, order_index, "
                "state_label, pet_behavior, pet_emotion, semantic_anchor, interaction_prompt, "
                "created_at) VALUES(?, ?, ?, 0, 'state1', 'sit', 'happy', 'anchor1', "
                "'prompt1', datetime('now'))",
                (scene_id_1, destination["id"], spec_id),
            )

        # 尝试插入第二个 scene_plan，也使用 order_index=0（应失败）
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            with repo.transaction() as conn:
                scene_id_2 = new_id("scene")
                conn.execute(
                    "INSERT INTO scene_plans(scene_id, destination_id, spec_id, order_index, "
                    "state_label, pet_behavior, pet_emotion, semantic_anchor, interaction_prompt, "
                    "created_at) VALUES(?, ?, ?, 0, 'state2', 'run', 'excited', 'anchor2', "
                    "'prompt2', datetime('now'))",
                    (scene_id_2, destination["id"], spec_id),
                )

        # 测试：同一 session 的 clarification_input 不能有重复的 input_id
        run = storage.create_run(
            api_client_id=client_id,
            session_id=session["id"],
            request_input={"text": "test"},
            response_format={"type": "text"},
            idempotency_key="test_key_unique",
            idempotency_body_hash="test_hash_unique",
        )

        input_id = new_id("input")
        with repo.transaction() as conn:
            conn.execute(
                "INSERT INTO clarification_inputs(input_id, session_id, run_id, "
                "raw_text, classification, created_at) VALUES(?, ?, ?, 'text1', 'empty', "
                "datetime('now'))",
                (input_id, session["id"], run["id"]),
            )

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            with repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO clarification_inputs(input_id, session_id, run_id, "
                    "raw_text, classification, created_at) VALUES(?, ?, ?, 'text2', 'empty', "
                    "datetime('now'))",
                    (input_id, session["id"], run["id"]),
                )

        storage.close()
        repo.close()


# ============================================================================
# 额外测试：CHECK 约束验证
# ============================================================================


def test_check_constraints_are_enforced():
    """额外测试：CHECK 约束验证（phase 枚举、order_index 范围等）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        storage = Storage(db_path, recover=False)
        repo = DestinationRepository(db_path)
        repo.open()

        client_id = storage.upsert_api_client("test_hash", "test_client")
        session = storage.create_session(client_id)

        # 测试：phase 必须是有效枚举值
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            with repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO destinations(id, session_id, api_client_id, phase, "
                    "done, created_at, updated_at) VALUES(?, ?, ?, 'invalid_phase', 0, "
                    "datetime('now'), datetime('now'))",
                    (new_id("destination"), session["id"], client_id),
                )

        # 测试：order_index 只能是 0 或 1
        destination = repo.create_destination(
            session_id=session["id"],
            api_client_id=client_id,
        )

        with repo.transaction() as conn:
            requirements_id = new_id("requirements")
            conn.execute(
                "INSERT INTO destination_requirements(requirements_id, destination_id, "
                "source_inputs, frozen_at, sha256, created_at) "
                "VALUES(?, ?, '[]', datetime('now'), 'test_sha', datetime('now'))",
                (requirements_id, destination["id"]),
            )

            spec_id = new_id("spec")
            conn.execute(
                "INSERT INTO destination_specs(spec_id, destination_id, spec_version, "
                "template_id, template_version, requirements_id, requirements_sha256, "
                "title, shared_environment_spec, locked_at, sha256, created_at) "
                "VALUES(?, ?, 1, 'template1', 'v1', ?, 'sha256', 'Test', '{}', "
                "datetime('now'), 'spec_sha', datetime('now'))",
                (spec_id, destination["id"], requirements_id),
            )

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            with repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO scene_plans(scene_id, destination_id, spec_id, order_index, "
                    "state_label, pet_behavior, pet_emotion, semantic_anchor, interaction_prompt, "
                    "created_at) VALUES(?, ?, ?, 2, 'state', 'sit', 'happy', 'anchor', "
                    "'prompt', datetime('now'))",
                    (new_id("scene"), destination["id"], spec_id),
                )

        storage.close()
        repo.close()
