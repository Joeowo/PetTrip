"""会话2 Snapshot Builder 测试。

覆盖: 生成的 Snapshot 通过 contracts JSON Schema、取值与固定模板一致、
资产清单读真实 PNG 尺寸、Snapshot 不含绝对路径或模型字段。
"""

from __future__ import annotations

import json
from pathlib import Path

from content_service import (
    build_asset_manifest,
    build_snapshot,
    default_world_spec,
    plan_scene,
    validate_snapshot,
)

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"


def _build():
    spec = default_world_spec()
    plan = plan_scene(spec)
    return build_snapshot(plan, spec)


def test_snapshot_passes_contracts_schema():
    validate_snapshot(_build())  # 不抛即通过


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
    manifest = build_asset_manifest(plan_scene(default_world_spec()), ASSET_DIR)
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
