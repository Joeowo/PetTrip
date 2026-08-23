"""会话3 快照交付服务。

从已通过付费流水线的 run 目录读取 SceneSnapshot 与 PNG，用与会话2 相同的
稳定 URI 提供；不重新调用 Responses 或 Images。run 目录必须存在
content-ready.json 标记，缺少标记的目录一律拒绝服务。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response


def load_run(run_dir: Path) -> dict:
    """校验 run 目录处于 content-ready 状态并读取交付数据。"""
    if not run_dir.is_dir():
        raise ValueError("run directory does not exist: " + run_dir.name)
    marker = run_dir / "content-ready.json"
    if not marker.is_file():
        raise ValueError("run directory is not content-ready: " + run_dir.name)
    try:
        snapshot = json.loads((run_dir / "scene-snapshot.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "asset-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("run directory deliverables are unreadable: " + str(exc)) from exc
    return {
        "run_id": run_dir.name,
        "snapshot": snapshot,
        "manifest": manifest,
        "asset_dir": run_dir / "assets",
    }


def create_app(run_dir: Path) -> FastAPI:
    data = load_run(run_dir)
    app = FastAPI(title="PetTrip session3 snapshot delivery")
    app.state.run_id = data["run_id"]
    snapshot = data["snapshot"]
    manifest = data["manifest"]
    asset_dir = data["asset_dir"]
    declared_asset_ids = {entry["asset_id"] for entry in manifest["assets"]}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/run-id")
    def run_id_endpoint() -> dict:
        return {"run_id": data["run_id"]}

    @app.get("/snapshot")
    def get_snapshot() -> JSONResponse:
        return JSONResponse(content=snapshot)

    @app.get("/manifest")
    def get_manifest() -> JSONResponse:
        return JSONResponse(content=manifest)

    @app.get("/assets/{asset_id}.png")
    def get_asset(asset_id: str) -> Response:
        # 白名单：仅 manifest 声明的 asset_id，拒绝未知值与路径穿越
        if asset_id not in declared_asset_ids:
            raise HTTPException(status_code=404, detail="unknown asset_id")
        path = asset_dir / f"{asset_id}.png"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="asset file missing")
        return Response(content=path.read_bytes(), media_type="image/png")

    return app
