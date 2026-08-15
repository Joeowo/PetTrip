"""v0.2 契约与构建器单测：placed_prefab 语义、schema 选择与业务字段比较。"""

from __future__ import annotations

import json

import pytest

from content_service.models import BuildSlot, Point, SceneSnapshot
from content_service.snapshot_builder import (
    business_fields_equal,
    build_snapshot,
    validate_snapshot_dict,
)
from tests.conftest import source_run_dir  # noqa: F401


def _minimal_snapshot(placed: str | None) -> SceneSnapshot:
    slot = BuildSlot(
        id="small_shelter",
        position=Point(x=430, y=96),
        allowed_prefabs=["small_shelter"],
        placed_prefab=placed,
    )
    return SceneSnapshot(
        schema_version="0.2",
        scene_id="session1_beach",
        canvas={"width": 512, "height": 288, "pixels_per_unit": 16},
        layers=[
            {"id": "background", "asset_id": "beach_background", "sorting_order": 0, "position": {"x": 256, "y": 144}},
            {"id": "lighthouse", "asset_id": "lighthouse", "sorting_order": 10, "position": {"x": 112, "y": 168}},
            {"id": "pet", "asset_id": "pet", "sorting_order": 20, "position": {"x": 250, "y": 112}},
        ],
        activity_zone={
            "id": "beach_foreground",
            "type": "polygon",
            "points": [
                {"x": 48, "y": 48},
                {"x": 464, "y": 48},
                {"x": 464, "y": 160},
                {"x": 48, "y": 160},
            ],
        },
        interactions=[{"id": "pet_wave", "kind": "pet_action", "anchor": {"x": 250, "y": 136}, "radius": 24}],
        build_slots=[slot],
    )


def test_v02_schema_accepts_placement_states() -> None:
    validate_snapshot_dict(_minimal_snapshot(None).model_dump(mode="json", exclude_none=True))
    validate_snapshot_dict(_minimal_snapshot("small_shelter").model_dump(mode="json", exclude_none=True))


def test_v02_schema_rejects_unknown_prefab() -> None:
    data = _minimal_snapshot("spaceship").model_dump(mode="json", exclude_none=True)
    with pytest.raises(Exception, match="not one of"):
        validate_snapshot_dict(data)


def test_v02_schema_rejects_placement_in_v01() -> None:
    data = _minimal_snapshot("small_shelter").model_dump()
    data["schema_version"] = "0.1"
    with pytest.raises(Exception):
        validate_snapshot_dict(data)


def test_v01_snapshot_still_validates(source_run_dir) -> None:  # noqa: F811, ANN001
    legacy = json.loads((source_run_dir / "scene-snapshot.json").read_text(encoding="utf-8"))
    validate_snapshot_dict(legacy)


def test_business_fields_equal_ignores_only_placement() -> None:
    empty = _minimal_snapshot(None)
    placed = _minimal_snapshot("small_shelter")
    assert business_fields_equal(empty, placed)
    moved = _minimal_snapshot(None)
    moved.layers[2].position = Point(x=400, y=112)
    assert not business_fields_equal(empty, moved)


def test_build_snapshot_rejects_unallowed_placement() -> None:
    from content_service.models import WorldSpec
    from content_service.snapshot_builder import plan_scene

    spec = WorldSpec(
        theme="seaside",
        landmark="lighthouse",
        interaction_id="pet_wave",
        build_slot_id="small_shelter",
        forbidden_objects=["vehicle"],
        canvas_width=512,
        canvas_height=288,
        pixels_per_unit=16,
    )
    plan = plan_scene(spec)
    with pytest.raises(ValueError, match="not allowed"):
        build_snapshot(plan, _manifest_stub(), spec, placed_prefab="rocket")


def _manifest_stub():
    from content_service.models import AssetEntry, AssetManifest

    def entry(asset_id: str) -> AssetEntry:
        return AssetEntry(
            asset_id=asset_id,
            filename=f"{asset_id}.png",
            uri=f"/assets/{asset_id}.png",
            width=32,
            height=32,
            channels=4,
            anchor=Point(x=0, y=0),
            sha256="0" * 64,
        )

    return AssetManifest(assets=[entry("beach_background"), entry("lighthouse"), entry("pet"), entry("small_shelter")])
