"""Snapshot Builder: 固定 WorldSpec -> ScenePlan -> AssetManifest -> SceneSnapshot。

会话2 用固定模板生成（不调用任何模型）；生成的 SceneSnapshot 必须通过
contracts/scene-snapshot/v0.1.schema.json 校验，且不含绝对路径或生成模型字段。

AssetManifest 必须参与构建：ScenePlan 引用的每个 asset_id / build_slot prefab 都要
在 manifest 中声明，否则拒绝发布 Snapshot——确保 manifest 与发布的 Snapshot 一致，
不让 manifest 沦为摆设。
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
from jsonschema import ValidationError, validate
from PIL import Image

from .models import (
    ActivityZone,
    ActivityZonePlan,
    AssetEntry,
    AssetManifest,
    BuildSlot,
    BuildSlotPlan,
    Canvas,
    Interaction,
    InteractionPlan,
    Layer,
    LayerPlan,
    Point,
    ScenePlan,
    SceneSnapshot,
    WorldSpec,
)

# content_service/ -> session2/ -> pilot4mvp/ -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "contracts" / "scene-snapshot" / "v0.1.schema.json"


def default_world_spec() -> WorldSpec:
    """会话2 固定的海边灯塔世界意图。"""
    return WorldSpec()


def plan_scene(spec: WorldSpec) -> ScenePlan:
    """固定海边灯塔场景模板；取值与 contracts schema 的 const 约束对齐。"""
    return ScenePlan(
        scene_id="session1_beach",
        layers=[
            LayerPlan(id="background", asset_id="beach_background", sorting_order=0, x=256, y=144),
            LayerPlan(id="lighthouse", asset_id="lighthouse", sorting_order=10, x=112, y=168),
            LayerPlan(id="pet", asset_id="pet", sorting_order=20, x=250, y=112),
        ],
        activity_zone=ActivityZonePlan(
            id="beach_foreground",
            type="polygon",
            points=[(48, 48), (464, 48), (464, 160), (48, 160)],
        ),
        interactions=[
            InteractionPlan(id="pet_wave", kind="pet_action", x=250, y=136, radius=24),
        ],
        build_slots=[
            BuildSlotPlan(id="small_shelter", x=430, y=96, allowed_prefabs=["small_shelter"]),
        ],
    )


def build_asset_manifest(plan: ScenePlan, asset_dir: Path) -> AssetManifest:
    """读取 PNG 实际尺寸生成资产清单（按出现顺序去重）。

    Pillow 读取尺寸；OpenCV 解码校验完整性——证明 OpenCV 参与会话2 资产链路
    （会话3 将用 Pillow/OpenCV 做图片规范化，会话2 先打通解码通路）。
    """
    needed: list[str] = []
    for layer in plan.layers:
        if layer.asset_id not in needed:
            needed.append(layer.asset_id)
    for slot in plan.build_slots:
        for prefab in slot.allowed_prefabs:
            if prefab not in needed:
                needed.append(prefab)

    entries: list[AssetEntry] = []
    for asset_id in needed:
        path = asset_dir / f"{asset_id}.png"
        with Image.open(path) as image:
            width, height = image.size
        # OpenCV 解码完整性校验：imread 对损坏/非图片返回 None
        matrix = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if matrix is None:
            raise ValueError("OpenCV could not decode asset png: " + asset_id)
        entries.append(
            AssetEntry(asset_id=asset_id, filename=f"{asset_id}.png", width=width, height=height)
        )
    return AssetManifest(assets=entries)


def build_snapshot(plan: ScenePlan, manifest: AssetManifest, spec: WorldSpec) -> SceneSnapshot:
    """由 ScenePlan、AssetManifest 与 WorldSpec 组装最终 SceneSnapshot。

    AssetManifest 参与构建：plan 引用的每个 layer.asset_id 与 build_slot prefab 必须在
    manifest 中声明，否则抛 ValueError——确保 manifest 与发布 Snapshot 一致。
    """
    declared = {entry.asset_id for entry in manifest.assets}

    for layer in plan.layers:
        if layer.asset_id not in declared:
            raise ValueError(
                "Layer asset is not declared in the manifest: "
                + layer.asset_id + " (layer " + layer.id + ")"
            )
    for slot in plan.build_slots:
        for prefab in slot.allowed_prefabs:
            if prefab not in declared:
                raise ValueError(
                    "Build slot prefab is not declared in the manifest: "
                    + prefab + " (slot " + slot.id + ")"
                )

    return SceneSnapshot(
        schema_version="0.1",
        scene_id=plan.scene_id,
        canvas=Canvas(
            width=spec.canvas_width,
            height=spec.canvas_height,
            pixels_per_unit=spec.pixels_per_unit,
        ),
        layers=[
            Layer(
                id=layer.id,
                asset_id=layer.asset_id,
                sorting_order=layer.sorting_order,
                position=Point(x=layer.x, y=layer.y),
            )
            for layer in plan.layers
        ],
        activity_zone=ActivityZone(
            id=plan.activity_zone.id,
            type=plan.activity_zone.type,
            points=[Point(x=x, y=y) for x, y in plan.activity_zone.points],
        ),
        interactions=[
            Interaction(
                id=item.id,
                kind=item.kind,
                anchor=Point(x=item.x, y=item.y),
                radius=item.radius,
            )
            for item in plan.interactions
        ],
        build_slots=[
            BuildSlot(
                id=slot.id,
                position=Point(x=slot.x, y=slot.y),
                allowed_prefabs=slot.allowed_prefabs,
            )
            for slot in plan.build_slots
        ],
    )


def validate_snapshot_dict(data: dict) -> None:
    """用 contracts JSON Schema 校验 Snapshot dict；不符合则抛 ValidationError。"""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validate(instance=data, schema=schema)


def validate_snapshot(snapshot: SceneSnapshot) -> None:
    """用 contracts JSON Schema 校验 Snapshot；不符合则抛 ValidationError。"""
    validate_snapshot_dict(snapshot.model_dump())
