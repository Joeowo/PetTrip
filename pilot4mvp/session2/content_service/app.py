"""PetTrip 会话2 FastAPI 内容服务。

稳定 URI 提供 SceneSnapshot 和 PNG。asset_id 经约定 URI /assets/{asset_id}.png
引用，Snapshot 不含绝对路径或生成模型字段。persist=True 时把该次运行的
WorldSpec/ScenePlan/AssetManifest/SceneSnapshot 落盘为证据。

采用工厂模式：模块不创建顶层 app，避免导入副作用。测试用 create_app(persist=False)，
正式运行由 run_server.py 以 persist=True 启动。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from .models import AssetManifest, ScenePlan, SceneSnapshot, WorldSpec
from .snapshot_builder import (
    build_asset_manifest,
    build_snapshot,
    default_world_spec,
    plan_scene,
    validate_snapshot,
)

SESSION2_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = SESSION2_ROOT / "assets"
RUNS_DIR = SESSION2_ROOT.parent / "runs"  # pilot4mvp/runs


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"session2-{stamp}-{secrets.token_hex(2)}"


def _persist_run(
    run_dir: Path,
    spec: WorldSpec,
    plan: ScenePlan,
    manifest: AssetManifest,
    snapshot: SceneSnapshot,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "world-spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "scene-plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "asset-manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "scene-snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")


def create_app(persist: bool = False) -> FastAPI:
    """构建会话2 内容服务。persist=True 时把产物落盘到 pilot4mvp/runs/<run_id>/。"""
    app = FastAPI(title="PetTrip session2 content service")

    spec = default_world_spec()
    plan = plan_scene(spec)
    manifest = build_asset_manifest(plan, ASSET_DIR)
    snapshot = build_snapshot(plan, spec)
    validate_snapshot(snapshot)
    run_id = _new_run_id()
    if persist:
        _persist_run(RUNS_DIR / run_id, spec, plan, manifest, snapshot)

    app.state.spec = spec
    app.state.plan = plan
    app.state.manifest = manifest
    app.state.snapshot = snapshot
    app.state.run_id = run_id
    app.state.asset_dir = ASSET_DIR

    declared_asset_ids = {entry.asset_id for entry in manifest.assets}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/run-id")
    def run_id_endpoint() -> dict:
        return {"run_id": run_id}

    @app.get("/snapshot")
    def get_snapshot() -> JSONResponse:
        return JSONResponse(content=snapshot.model_dump())

    @app.get("/manifest")
    def get_manifest() -> JSONResponse:
        return JSONResponse(content=manifest.model_dump())

    @app.get("/assets/{asset_id}.png")
    def get_asset(asset_id: str) -> Response:
        # 白名单：仅 manifest 声明的 asset_id，拒绝未知值与路径穿越
        if asset_id not in declared_asset_ids:
            raise HTTPException(status_code=404, detail="unknown asset_id")
        path = ASSET_DIR / f"{asset_id}.png"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="asset file missing")
        return Response(content=path.read_bytes(), media_type="image/png")

    return app
