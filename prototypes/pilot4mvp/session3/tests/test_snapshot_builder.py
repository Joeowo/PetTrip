from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import ValidationError

from content_service.image_pipeline import copy_overlay_assets
from content_service.models import WorldSpec
from content_service.snapshot_builder import (
    build_asset_manifest,
    build_snapshot,
    plan_scene,
    sha256_file,
    validate_snapshot,
    validate_snapshot_dict,
)

SESSION2_ASSETS = Path(__file__).resolve().parents[2] / "session2" / "assets"
VALID_WORLD = WorldSpec(
    theme="seaside",
    landmark="lighthouse",
    interaction_id="pet_wave",
    build_slot_id="small_shelter",
    forbidden_objects=["vehicle"],
    canvas_width=512,
    canvas_height=288,
    pixels_per_unit=16,
)


def _assets(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    copy_overlay_assets(SESSION2_ASSETS, assets)
    shutil.copy2(SESSION2_ASSETS / "beach_background.png", assets / "beach_background.png")
    return assets


def test_plan_scene_is_deterministic_and_uses_strict_world_spec() -> None:
    first = plan_scene(VALID_WORLD)
    second = plan_scene(VALID_WORLD)
    assert first == second
    assert first.interactions[0].id == VALID_WORLD.interaction_id
    assert first.build_slots[0].id == VALID_WORLD.build_slot_id


def test_manifest_remeasures_every_file_and_hash(tmp_path) -> None:
    assets = _assets(tmp_path)
    manifest = build_asset_manifest(plan_scene(VALID_WORLD), assets)
    by_id = {entry.asset_id: entry for entry in manifest.assets}
    assert set(by_id) == {"beach_background", "lighthouse", "pet", "small_shelter"}
    for asset_id, entry in by_id.items():
        path = assets / entry.filename
        assert entry.uri == f"/assets/{asset_id}.png"
        assert entry.mime_type == "image/png"
        assert entry.channels in {3, 4}
        assert entry.sha256 == sha256_file(path)
    assert (by_id["beach_background"].width, by_id["beach_background"].height) == (512, 288)


def test_snapshot_passes_schema_and_excludes_provider_fields(tmp_path) -> None:
    assets = _assets(tmp_path)
    plan = plan_scene(VALID_WORLD)
    manifest = build_asset_manifest(plan, assets)
    snapshot = build_snapshot(plan, manifest, VALID_WORLD)
    validate_snapshot(snapshot)
    text = json.dumps(snapshot.model_dump(), ensure_ascii=False)
    for forbidden in ("prompt", "model", "api_key", "sha256", "filename", "C:"):
        assert forbidden not in text


def test_snapshot_rejects_missing_manifest_asset(tmp_path) -> None:
    assets = _assets(tmp_path)
    plan = plan_scene(VALID_WORLD)
    manifest = build_asset_manifest(plan, assets)
    manifest.assets = [entry for entry in manifest.assets if entry.asset_id != "pet"]
    with pytest.raises(ValueError, match="manifest"):
        build_snapshot(plan, manifest, VALID_WORLD)


def test_schema_rejects_unknown_asset(tmp_path) -> None:
    assets = _assets(tmp_path)
    plan = plan_scene(VALID_WORLD)
    manifest = build_asset_manifest(plan, assets)
    data = build_snapshot(plan, manifest, VALID_WORLD).model_dump()
    data["layers"][0]["asset_id"] = "vehicle"
    with pytest.raises(ValidationError):
        validate_snapshot_dict(data)
