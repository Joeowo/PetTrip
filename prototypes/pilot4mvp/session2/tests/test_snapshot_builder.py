"""会话2 Snapshot Builder 测试。

覆盖正例: Snapshot 通过 contracts JSON Schema、取值与固定模板一致、资产清单读真实
PNG 尺寸、Snapshot 不含绝对路径或模型字段。
负例: AssetManifest 缺资产时拒绝构建(P1-1)；Schema 对缺字段/未知 asset/错版本/额外
模型字段拒绝(P2-1)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from content_service import (
    build_asset_manifest,
    build_snapshot,
    default_world_spec,
    plan_scene,
    validate_snapshot,
    validate_snapshot_dict,
)

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"


def _plan():
    return plan_scene(default_world_spec())


def _manifest():
    return build_asset_manifest(_plan(), ASSET_DIR)


def _build():
    return build_snapshot(_plan(), _manifest(), default_world_spec())


def test_snapshot_passes_contracts_schema():
    validate_snapshot(_build())


def test_snapshot_matches_fixed_template_values():
    snapshot = _build()
    assert snapshot.schema_version == "0.1"
    assert snapshot.scene_id == "session1_beach"
    assert snapshot.canvas.width == 512
    assert snapshot.canvas.height == 288
    assert snapshot.canvas.pixels_per_unit == 16
    assert [layer.asset_id for layer in snapshot.layers] == [
        "beach_background",
        "lighthouse",
        "pet",
    ]
    assert snapshot.interactions[0].id == "pet_wave"
    assert snapshot.interactions[0].radius == 24
    assert snapshot.build_slots[0].id == "small_shelter"
    assert snapshot.build_slots[0].allowed_prefabs == ["small_shelter"]


def test_asset_manifest_reads_real_png_sizes():
    manifest = _manifest()
    by_id = {entry.asset_id: entry for entry in manifest.assets}
    assert by_id["beach_background"].width == 512
    assert by_id["beach_background"].height == 288
    assert by_id["lighthouse"].width == 80 and by_id["lighthouse"].height == 160
    assert by_id["small_shelter"].width == 96 and by_id["small_shelter"].height == 72


def test_snapshot_has_no_absolute_path_or_model_fields():
    """会话2 通过门槛：Snapshot 不得含绝对路径或生成模型字段。"""
    text = json.dumps(_build().model_dump(), ensure_ascii=False)
    assert "C:" not in text
    assert ":/" not in text
    assert "\\\\" not in text
    for forbidden in ("prompt", "model", "openai", "image_url", "api_key"):
        assert forbidden not in text


# --- P1-1: AssetManifest 必须参与构建 ---
def test_build_snapshot_rejects_layer_asset_missing_from_manifest():
    plan = _plan()
    manifest = _manifest()
    manifest.assets = [entry for entry in manifest.assets if entry.asset_id != "pet"]
    with pytest.raises(ValueError, match="manifest"):
        build_snapshot(plan, manifest, default_world_spec())


def test_build_snapshot_rejects_prefab_missing_from_manifest():
    plan = _plan()
    manifest = _manifest()
    manifest.assets = [entry for entry in manifest.assets if entry.asset_id != "small_shelter"]
    with pytest.raises(ValueError, match="manifest"):
        build_snapshot(plan, manifest, default_world_spec())


# --- P2-1: contracts JSON Schema 负例 ---
def test_schema_rejects_missing_required_field():
    data = _build().model_dump()
    del data["canvas"]
    with pytest.raises(ValidationError):
        validate_snapshot_dict(data)


def test_schema_rejects_unknown_asset():
    data = _build().model_dump()
    data["layers"][0]["asset_id"] = "vehicle"
    with pytest.raises(ValidationError):
        validate_snapshot_dict(data)


def test_schema_rejects_wrong_version():
    data = _build().model_dump()
    data["schema_version"] = "9.9"
    with pytest.raises(ValidationError):
        validate_snapshot_dict(data)


def test_schema_rejects_extra_model_field():
    """additionalProperties:false 必须拒绝模型/路径等额外字段。"""
    data = _build().model_dump()
    data["model"] = "image-2"
    with pytest.raises(ValidationError):
        validate_snapshot_dict(data)
