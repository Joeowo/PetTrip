"""会话4 上游输入验收：既有成功 Snapshot 必须存在并被实际消费为基线。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from content_service.replay import ReplayError, materialize_run
from content_service.run_store import RunStore, RunStoreError
from tests.conftest import DEFAULT_INPUT, Env, source_run_dir  # noqa: F401


@pytest.fixture
def store(env: Env) -> RunStore:
    return env.store


def test_create_run_requires_source_snapshot(store: RunStore, tmp_path: Path, source_run_dir) -> None:  # noqa: F811, ANN001
    """源 run 缺少成功 Snapshot 时必须拒绝，符合规格"缺失则停止"。"""
    incomplete = tmp_path / "incomplete-source"
    incomplete.mkdir()
    for name in ("world-spec.json", "scene-plan.json"):
        shutil.copy2(source_run_dir / name, incomplete / name)
    shutil.copytree(source_run_dir / "assets", incomplete / "assets")

    with pytest.raises(RunStoreError, match="scene-snapshot"):
        store.create_run("session4-nosnap", DEFAULT_INPUT, incomplete)
    assert not (store.state_dir / "session4-nosnap").exists()


def test_create_run_copies_source_snapshot_as_baseline(store: RunStore, source_run_dir) -> None:  # noqa: F811, ANN001
    run_dir = store.create_run("session4-baseline", DEFAULT_INPUT, source_run_dir)
    copied = run_dir / "source-scene-snapshot.json"
    assert copied.is_file()
    original = json.loads((source_run_dir / "scene-snapshot.json").read_text(encoding="utf-8"))
    assert json.loads(copied.read_text(encoding="utf-8")) == original


def test_materialize_compares_against_source_snapshot(store: RunStore, source_run_dir) -> None:  # noqa: F811, ANN001
    run_dir = store.create_run("session4-verify", DEFAULT_INPUT, source_run_dir)
    summary = materialize_run(store, "session4-verify")
    assert summary["snapshot"] == "scene-snapshot.json"
    rebuilt = json.loads((run_dir / "scene-snapshot.json").read_text(encoding="utf-8"))
    # 重建快照与源成功快照业务字段一致（仅版本与放置状态不同）
    source = json.loads((source_run_dir / "scene-snapshot.json").read_text(encoding="utf-8"))
    assert rebuilt["layers"] == source["layers"]
    assert rebuilt["activity_zone"] == source["activity_zone"]
    assert rebuilt["interactions"] == source["interactions"]
    assert rebuilt["build_slots"][0]["position"] == source["build_slots"][0]["position"]


def test_materialize_fails_when_source_snapshot_tampered(store: RunStore, source_run_dir, tmp_path) -> None:  # noqa: F811, ANN001
    """fail-closed：源成功快照被篡改后，重建结果与基线不一致必须拒绝物化。"""
    tampered_source = tmp_path / "tampered-source"
    tampered_source.mkdir()
    for name in ("world-spec.json", "scene-plan.json"):
        shutil.copy2(source_run_dir / name, tampered_source / name)
    shutil.copytree(source_run_dir / "assets", tampered_source / "assets")
    snapshot = json.loads((source_run_dir / "scene-snapshot.json").read_text(encoding="utf-8"))
    snapshot["layers"][2]["position"]["x"] = 490  # 篡改宠物位置
    (tampered_source / "scene-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")

    run_dir = store.create_run("session4-tamper", DEFAULT_INPUT, tampered_source)
    with pytest.raises(ReplayError, match="source run's successful snapshot"):
        materialize_run(store, "session4-tamper")
    # 未物化：无 content-ready 标记
    assert not (run_dir / "content-ready.json").exists()


def test_materialize_fails_when_source_snapshot_carries_model_fields(
    store: RunStore, source_run_dir, tmp_path
) -> None:  # noqa: F811, ANN001
    """fail-closed：源快照携带 prompt 等模型私有字段（v0.1 契约的
    additionalProperties: false 禁止项）必须拒绝——Schema 校验必须先于
    Pydantic 构造，否则未知字段被静默丢弃造成假阳性。"""
    polluted_source = tmp_path / "polluted-source"
    polluted_source.mkdir()
    for name in ("world-spec.json", "scene-plan.json"):
        shutil.copy2(source_run_dir / name, polluted_source / name)
    shutil.copytree(source_run_dir / "assets", polluted_source / "assets")
    snapshot = json.loads((source_run_dir / "scene-snapshot.json").read_text(encoding="utf-8"))
    snapshot["prompt"] = "生成一个海边场景"  # 模型私有字段，跨界契约禁止
    (polluted_source / "scene-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")

    run_dir = store.create_run("session4-polluted", DEFAULT_INPUT, polluted_source)
    with pytest.raises(ReplayError, match="source snapshot failed validation"):
        materialize_run(store, "session4-polluted")
    assert not (run_dir / "content-ready.json").exists()
