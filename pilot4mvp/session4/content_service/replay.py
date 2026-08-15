"""会话4 离线重建：仅从 run 目录既有 artifact 重建 SceneSnapshot 与资产清单。

重建链路 world-spec.json -> plan_scene -> build_asset_manifest（实测 PNG 哈希）
-> build_snapshot -> 契约校验。全程不 import 外部模型客户端，也不读取任何
OPENAI_* 环境变量；与 Responses/Images 的隔离由代码路径保证。

放置状态来自 placement.json（Unity 上传 v2 时服务端记录的动作）。重放时
重建结果必须与磁盘上既有快照完全一致，否则拒绝写回（fail-closed）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import AssetManifest, SceneSnapshot, WorldSpec
from .run_store import RunStore, RunStoreError
from .snapshot_builder import (
    business_fields_equal,
    build_asset_manifest,
    build_snapshot,
    plan_scene,
    sha256_bytes,
    validate_snapshot,
)

PLACEMENT_NAME = "placement.json"
SNAPSHOT_V1_NAME = "scene-snapshot.json"
SNAPSHOT_V2_NAME = "scene-snapshot-v2.json"
MANIFEST_NAME = "asset-manifest.json"


class ReplayError(Exception):
    """面向调用方的可预期错误，服务层映射为 4xx。"""


def load_placement(run_dir: Path) -> str | None:
    path = run_dir / PLACEMENT_NAME
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("prefab_id")


def rebuild_snapshot(run_dir: Path, placed_prefab: str | None = None) -> tuple[SceneSnapshot, AssetManifest]:
    """从 run 目录 artifact 确定性重建 Snapshot 与 manifest。"""
    spec_path = run_dir / "world-spec.json"
    if not spec_path.is_file():
        raise ReplayError("run directory is missing world-spec.json")
    try:
        spec = WorldSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ReplayError("world-spec.json failed validation: " + str(exc)) from exc

    plan = plan_scene(spec)
    plan_path = run_dir / "scene-plan.json"
    if plan_path.is_file():
        stored_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan.model_dump(mode="json") != stored_plan:
            raise ReplayError("rebuilt scene plan differs from stored scene-plan.json")

    manifest = build_asset_manifest(plan, run_dir / "assets")
    snapshot = build_snapshot(plan, manifest, spec, placed_prefab=placed_prefab)
    try:
        validate_snapshot(snapshot)
    except Exception as exc:  # noqa: BLE001
        raise ReplayError("rebuilt snapshot failed contract validation: " + str(exc)) from exc
    return snapshot, manifest


def _write_json(path: Path, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")
    return payload


def materialize_run(store: RunStore, run_id: str) -> dict:
    """统一输入后首次物化：重建 v1 快照与 manifest，标记 content-ready。

    重建结果必须与源 run 的既有成功 Snapshot（source-scene-snapshot.json）业务
    字段一致（放置状态与 schema 版本除外）——实际消费上游成功产物作为基线，
    而不是仅靠 world-spec 自证。
    """
    try:
        run_dir = store.run_dir(run_id)
    except RunStoreError as exc:
        raise ReplayError(str(exc)) from exc
    snapshot, manifest = rebuild_snapshot(run_dir, placed_prefab=None)

    baseline_path = run_dir / "source-scene-snapshot.json"
    if baseline_path.is_file():
        try:
            baseline = SceneSnapshot.model_validate_json(baseline_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ReplayError("source snapshot failed validation: " + str(exc)) from exc
        if not business_fields_equal(baseline, snapshot):
            raise ReplayError(
                "rebuilt snapshot business fields differ from the source run's successful snapshot"
            )
    else:
        raise ReplayError("source-scene-snapshot.json is missing; cannot verify against upstream success")

    _write_json(run_dir / MANIFEST_NAME, manifest.model_dump(mode="json"))
    _write_json(run_dir / SNAPSHOT_V1_NAME, snapshot.model_dump(mode="json", exclude_none=True))
    store.mark_content_ready(run_id, SNAPSHOT_V1_NAME)
    digest = sha256_bytes((run_dir / SNAPSHOT_V1_NAME).read_bytes())
    return {"run_id": run_id, "snapshot": SNAPSHOT_V1_NAME, "sha256": digest}


def replay_run(store: RunStore, run_id: str) -> dict:
    """重建当前活动版本快照，与既有落盘版本一致后写回并记 job.replayed。"""
    try:
        run_dir = store.require_content_ready(run_id)
    except RunStoreError as exc:
        raise ReplayError(str(exc)) from exc

    placed = load_placement(run_dir)
    snapshot, manifest = rebuild_snapshot(run_dir, placed_prefab=placed)
    active_name = store.active_snapshot_name(run_id)
    existing_path = run_dir / active_name
    if existing_path.is_file():
        existing = SceneSnapshot.model_validate_json(existing_path.read_text(encoding="utf-8"))
        if not business_fields_equal(existing, snapshot):
            raise ReplayError("rebuilt snapshot business fields differ from stored " + active_name)
        if existing.model_dump() != snapshot.model_dump():
            raise ReplayError("rebuilt snapshot placement differs from stored " + active_name)
    _write_json(run_dir / MANIFEST_NAME, manifest.model_dump(mode="json"))
    _write_json(run_dir / active_name, snapshot.model_dump(mode="json", exclude_none=True))
    digest = sha256_bytes(existing_path.read_bytes())
    store.append_event(
        run_id,
        "job.replayed",
        {"snapshot": active_name, "sha256": digest, "model_calls": "none"},
    )
    return {"run_id": run_id, "snapshot": active_name, "sha256": digest, "business_fields_match": True}
