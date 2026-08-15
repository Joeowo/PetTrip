"""会话4 内容服务：统一输入、快照交付、v2 槽位放置、验证报告与离线重放。

在会话3 只读交付服务的基础上新增：
- POST /runs                    统一输入（run_id + input），从源 run artifact 物化新 run
- POST /runs/{id}/snapshot-v2   Unity 上传放置后的 v2 快照（v0.2 契约 + 一致性校验）
- POST /runs/{id}/reports       Unity 验证报告（run_id / Snapshot 哈希强校验）写 SQLite
- POST /runs/{id}/replay        仅从既有 artifact 重建快照（job.replayed）
- GET  /runs/{id}               按 run_id 查询 SQLite 事件与报告

本服务不 import 外部模型客户端，代码路径上不可能发起 Responses/Images 请求。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .models import RunRequest, SceneSnapshot, UnityReport
from .replay import (
    PLACEMENT_NAME,
    SNAPSHOT_V2_NAME,
    ReplayError,
    materialize_run,
    replay_run,
)
from .run_store import RunStore, RunStoreError, decode_base64_png
from .snapshot_builder import business_fields_equal, validate_snapshot, validate_snapshot_dict


def create_app(
    source_run_dir: Path,
    state_dir: Path,
    db_path: Path,
    run_id: str | None = None,
) -> FastAPI:
    app = FastAPI(title="PetTrip session4 content service")
    store = RunStore(state_dir, db_path)
    app.state.run_store = store
    app.state.source_run_dir = source_run_dir
    app.state.run_id = run_id
    if run_id is not None:
        store.require_content_ready(run_id)

    # ---------- 错误映射 ----------

    def _fail(exc: Exception, status: int) -> HTTPException:
        return HTTPException(status_code=status, detail=str(exc))

    def _require_active() -> str:
        if app.state.run_id is None:
            raise HTTPException(status_code=503, detail="no active run; POST /runs first")
        return app.state.run_id

    # ---------- 交付（与会话2/3 相同的稳定 URI）----------

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "run_id": app.state.run_id}

    @app.get("/run-id")
    def run_id_endpoint() -> dict:
        return {"run_id": _require_active()}

    @app.get("/snapshot")
    def get_snapshot() -> JSONResponse:
        active = _require_active()
        try:
            run_dir = store.require_content_ready(active)
            name = store.active_snapshot_name(active)
        except RunStoreError as exc:
            raise _fail(exc, 409) from exc
        return JSONResponse(content=json.loads((run_dir / name).read_text(encoding="utf-8")))

    @app.get("/snapshot-meta")
    def get_snapshot_meta() -> dict:
        active = _require_active()
        try:
            run_dir = store.require_content_ready(active)
            name = store.active_snapshot_name(active)
            data = json.loads((run_dir / name).read_text(encoding="utf-8"))
        except RunStoreError as exc:
            raise _fail(exc, 409) from exc
        return {
            "run_id": active,
            "snapshot": name,
            "schema_version": data["schema_version"],
            "sha256": store.snapshot_sha256(active),
        }

    @app.get("/manifest")
    def get_manifest() -> JSONResponse:
        active = _require_active()
        try:
            run_dir = store.require_content_ready(active)
        except RunStoreError as exc:
            raise _fail(exc, 409) from exc
        return JSONResponse(content=json.loads((run_dir / "asset-manifest.json").read_text(encoding="utf-8")))

    @app.get("/assets/{asset_id}.png")
    def get_asset(asset_id: str) -> Response:
        active = _require_active()
        try:
            run_dir = store.require_content_ready(active)
            manifest = json.loads((run_dir / "asset-manifest.json").read_text(encoding="utf-8"))
        except RunStoreError as exc:
            raise _fail(exc, 409) from exc
        declared = {entry["asset_id"] for entry in manifest["assets"]}
        if asset_id not in declared:
            raise HTTPException(status_code=404, detail="unknown asset_id")
        path = run_dir / "assets" / f"{asset_id}.png"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="asset file missing")
        return Response(content=path.read_bytes(), media_type="image/png")

    # ---------- 会话4：统一输入 / v2 / 报告 / 重放 / 查询 ----------

    @app.post("/runs", status_code=201)
    def post_run(request: RunRequest) -> dict:
        try:
            store.create_run(request.run_id, request.input, app.state.source_run_dir)
            summary = materialize_run(store, request.run_id)
        except (RunStoreError, ReplayError) as exc:
            raise _fail(exc, 409) from exc
        app.state.run_id = request.run_id
        return {
            "run_id": request.run_id,
            "snapshot": summary["snapshot"],
            "snapshot_sha256": summary["sha256"],
        }

    @app.post("/runs/{run_id}/snapshot-v2", status_code=201)
    def post_snapshot_v2(run_id: str, payload: dict) -> dict:
        if not store.has_run(run_id):
            raise HTTPException(status_code=404, detail="unknown run_id")
        if run_id != app.state.run_id:
            raise HTTPException(status_code=409, detail="run is not the active run")
        try:
            run_dir = store.require_content_ready(run_id)
            current_name = store.active_snapshot_name(run_id)
            current = SceneSnapshot.model_validate_json(
                (run_dir / current_name).read_text(encoding="utf-8")
            )
        except (RunStoreError, ValidationError) as exc:
            raise _fail(exc, 409) from exc

        if payload.get("schema_version") != "0.2":
            raise HTTPException(status_code=422, detail="v2 snapshot must use schema_version 0.2")
        try:
            validate_snapshot_dict(payload)
        except jsonschema.ValidationError as exc:
            raise HTTPException(status_code=422, detail="v2 snapshot failed schema validation: " + exc.message) from exc
        try:
            candidate = SceneSnapshot.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="v2 snapshot failed model validation") from exc

        slot = candidate.build_slots[0]
        if slot.placed_prefab is None:
            raise HTTPException(status_code=422, detail="v2 snapshot must carry placed_prefab")
        if slot.placed_prefab not in slot.allowed_prefabs:
            raise HTTPException(status_code=422, detail="placed prefab is not allowed by the slot")
        if not business_fields_equal(candidate, current):
            raise HTTPException(
                status_code=422,
                detail="v2 changed business fields other than build slot placement",
            )

        (run_dir / SNAPSHOT_V2_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / PLACEMENT_NAME).write_text(
            json.dumps(
                {"slot_id": slot.id, "prefab_id": slot.placed_prefab},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        store.mark_content_ready(run_id, SNAPSHOT_V2_NAME)
        return {
            "run_id": run_id,
            "snapshot": SNAPSHOT_V2_NAME,
            "sha256": store.snapshot_sha256(run_id),
        }

    @app.post("/runs/{run_id}/reports", status_code=201)
    def post_report(run_id: str, report: UnityReport) -> dict:
        if not store.has_run(run_id):
            raise HTTPException(status_code=404, detail="unknown run_id")
        if run_id != app.state.run_id:
            raise HTTPException(status_code=409, detail="run is not the active run")
        expected = store.snapshot_sha256(run_id)
        if report.snapshot_sha256 != expected:
            raise HTTPException(
                status_code=409,
                detail="report snapshot_sha256 does not match the active snapshot",
            )
        try:
            screenshot = decode_base64_png(report.screenshot_png_base64)
            report_id = store.save_report(run_id, report.model_dump(mode="json"), screenshot)
        except RunStoreError as exc:
            raise _fail(exc, 422) from exc
        return {"report_id": report_id, "run_id": run_id, "snapshot_sha256": expected}

    @app.post("/runs/{run_id}/replay")
    def post_replay(run_id: str) -> dict:
        try:
            return replay_run(store, run_id)
        except ReplayError as exc:
            if "does not exist" in str(exc) or "not content-ready" in str(exc):
                raise _fail(exc, 404) from exc
            raise _fail(exc, 409) from exc

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        if not store.has_run(run_id):
            raise HTTPException(status_code=404, detail="unknown run_id")
        return {
            "run_id": run_id,
            "events": store.load_events(run_id),
            "reports": store.load_reports(run_id),
        }

    return app
