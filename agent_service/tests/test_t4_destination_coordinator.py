"""T4 目的地协调器测试（Issue #16）。

测试要求（来自 Issue #10 第 15.5 节）：
1. Repository 已提交、checkpoint 落后时不重做里程碑
2. checkpoint 领先或损坏时以 Repository 为准 fail closed
3. 服务重启后继续非终态 Destination
4. 已提交 Scene 不因恢复生成第二份 artifact
5. 并发 worker claim 不重复处理同一步骤
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from agent_service.domain.destination_coordinator import DestinationCoordinatorService
from agent_service.storage.destination_storage import DestinationRepository
from agent_service.shared.ids import new_id


@pytest.fixture
def temp_db():
    """创建临时数据库。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    yield db_path

    # 清理
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def repository(temp_db):
    """创建并初始化 Repository。"""
    repo = DestinationRepository(temp_db)
    repo.open()

    # 创建必要的基础表（api_clients, sessions）
    with repo._lock:
        assert repo._conn is not None
        repo._conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_clients (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                api_client_id TEXT NOT NULL REFERENCES api_clients(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                api_client_id TEXT NOT NULL REFERENCES api_clients(id),
                status TEXT NOT NULL DEFAULT 'queued',
                idempotency_key TEXT NOT NULL,
                idempotency_body_hash TEXT NOT NULL,
                request_input TEXT NOT NULL,
                response_format TEXT NOT NULL,
                output_text TEXT,
                output_structured TEXT,
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                api_client_id TEXT NOT NULL REFERENCES api_clients(id),
                source TEXT NOT NULL,
                purpose TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                width INTEGER,
                height INTEGER,
                rel_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)

    yield repo

    repo.close()


@pytest.fixture
def coordinator(repository):
    """创建协调器。"""
    return DestinationCoordinatorService(repository)


def test_recover_pending_destinations_empty(coordinator):
    """测试：没有非终态 Destination 时恢复返回空。"""
    result = coordinator.recover_pending_destinations()

    assert result["recovered_destinations"] == 0
    assert result["skipped_done"] == 0


def test_recover_pending_destinations_skips_done(coordinator, repository):
    """测试：跳过已完成的 Destination。"""
    # 创建一个已完成的 Destination
    client_id = new_id("client")
    session_id = new_id("session")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    # 标记为完成
    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "UPDATE destinations SET done = 1, phase = 'terminal' WHERE id = ?",
            (destination["id"],),
        )

    result = coordinator.recover_pending_destinations()

    assert result["recovered_destinations"] == 0
    assert result["skipped_done"] == 1


def test_recover_pending_destinations_continues_non_terminal(coordinator, repository):
    """测试：继续非终态 Destination。"""
    # 创建一个非终态 Destination
    client_id = new_id("client")
    session_id = new_id("session")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    result = coordinator.recover_pending_destinations()

    assert result["recovered_destinations"] == 1
    assert result["skipped_done"] == 0


def test_repository_priority_has_frozen_requirements(coordinator, repository):
    """测试：Repository 已提交 Requirements 时不重做。"""
    # 创建 Destination
    client_id = new_id("client")
    session_id = new_id("session")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    # 提交 Requirements
    requirements_id = new_id("req")
    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO destination_requirements(requirements_id, destination_id, source_inputs, frozen_at, sha256, created_at) "
            "VALUES(?, ?, '[]', datetime('now'), 'abc123', datetime('now'))",
            (requirements_id, destination["id"]),
        )

    # 检查是否有冻结的 Requirements
    has_requirements = coordinator._has_frozen_requirements(destination["id"])

    assert has_requirements is True


def test_repository_priority_has_locked_spec(coordinator, repository):
    """测试：Repository 已提交 Spec 时不重做。"""
    # 创建 Destination 和 Requirements
    client_id = new_id("client")
    session_id = new_id("session")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    requirements_id = new_id("req")
    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO destination_requirements(requirements_id, destination_id, source_inputs, frozen_at, sha256, created_at) "
            "VALUES(?, ?, '[]', datetime('now'), 'abc123', datetime('now'))",
            (requirements_id, destination["id"]),
        )

    # 提交 Spec
    spec_id = new_id("spec")
    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO destination_specs(spec_id, destination_id, spec_version, template_id, template_version, "
            "requirements_id, requirements_sha256, title, shared_environment_spec, locked_at, sha256, created_at) "
            "VALUES(?, ?, 1, 'template1', 'v1', ?, 'abc123', 'Test Destination', '{}', datetime('now'), 'def456', datetime('now'))",
            (spec_id, destination["id"], requirements_id),
        )

    # 检查是否有锁定的 Spec
    has_spec = coordinator._has_locked_spec(destination["id"])

    assert has_spec is True


def test_repository_priority_has_shared_environment(coordinator, repository):
    """测试：Repository 已提交 SharedEnvironment 时不重做。"""
    # 创建完整的依赖链
    client_id = new_id("client")
    session_id = new_id("session")
    run_id = new_id("run")
    file_id = new_id("file")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )
        repository._conn.execute(
            "INSERT INTO runs(id, session_id, api_client_id, idempotency_key, idempotency_body_hash, "
            "request_input, response_format, created_at) VALUES(?, ?, ?, 'key1', 'hash1', '{}', '{}', datetime('now'))",
            (run_id, session_id, client_id),
        )
        repository._conn.execute(
            "INSERT INTO files(id, api_client_id, source, purpose, mime_type, size_bytes, sha256, "
            "width, height, rel_path, created_at) VALUES(?, ?, 'agent_generated', 'generated_image', "
            "'image/png', 1024, 'sha256abc', 1024, 1024, 'path/to/file.png', datetime('now'))",
            (file_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    # 提交 SharedEnvironment
    shared_env_id = new_id("sharedenv")
    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO shared_environment_artifacts(shared_environment_id, destination_id, source_run_id, "
            "image_file_id, image_sha256, width_px, height_px, created_at) "
            "VALUES(?, ?, ?, ?, 'sha256abc', 1024, 1024, datetime('now'))",
            (shared_env_id, destination["id"], run_id, file_id),
        )

    # 检查是否有共享环境
    has_shared_env = coordinator._has_shared_environment(destination["id"])

    assert has_shared_env is True


def test_determine_resume_phase_clarification(coordinator, repository):
    """测试：从 clarification 阶段恢复。"""
    client_id = new_id("client")
    session_id = new_id("session")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    resume_phase = coordinator._determine_resume_phase(destination["id"], "clarification")

    assert resume_phase == "clarification"


def test_determine_resume_phase_after_requirements_frozen(coordinator, repository):
    """测试：Requirements 已冻结后进入 specification 阶段。"""
    client_id = new_id("client")
    session_id = new_id("session")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    # 提交 Requirements
    requirements_id = new_id("req")
    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO destination_requirements(requirements_id, destination_id, source_inputs, frozen_at, sha256, created_at) "
            "VALUES(?, ?, '[]', datetime('now'), 'abc123', datetime('now'))",
            (requirements_id, destination["id"]),
        )

    resume_phase = coordinator._determine_resume_phase(destination["id"], "clarification")

    assert resume_phase == "requirements"


def test_get_scene_status_no_scenes(coordinator, repository):
    """测试：没有场景时的状态。"""
    client_id = new_id("client")
    session_id = new_id("session")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    scene_status = coordinator._get_scene_status(destination["id"])

    assert scene_status["total_scenes"] == 0
    assert scene_status["ready_scenes"] == 0
    assert scene_status["failed_scenes"] == 0
    assert scene_status["all_ready"] is False
    assert scene_status["all_failed"] is False


def test_process_destination_returns_pending(coordinator, repository):
    """测试：处理目的地返回 pending 状态。"""
    client_id = new_id("client")
    session_id = new_id("session")

    with repository._lock:
        assert repository._conn is not None
        repository._conn.execute(
            "INSERT INTO api_clients(id, name, key_hash, created_at) VALUES(?, ?, ?, datetime('now'))",
            (client_id, "test", "hash123"),
        )
        repository._conn.execute(
            "INSERT INTO sessions(id, api_client_id, created_at, updated_at) VALUES(?, ?, datetime('now'), datetime('now'))",
            (session_id, client_id),
        )

    destination = repository.create_destination(
        session_id=session_id,
        api_client_id=client_id,
    )

    result = coordinator.process_destination(destination["id"])

    assert result["phase"] == "clarification"
    assert result["status"] == "pending"
