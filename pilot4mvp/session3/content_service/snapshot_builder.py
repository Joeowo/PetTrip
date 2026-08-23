"""会话3 确定性 ScenePlan、资产清单与 SceneSnapshot 构建。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
from jsonschema import validate
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

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "contracts" / "scene-snapshot" / "v0.1.schema.json"
ASSET_ANCHORS = {
    "beach_background": Point(x=256, y=144),
    "lighthouse": Point(x=112, y=168),
    "pet": Point(x=250, y=112),
    "small_shelter": Point(x=430, y=96),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_scene(spec: WorldSpec) -> ScenePlan:
    """将严格 WorldSpec 映射到阶段二已验证的固定坐标模板。"""
    if spec.interaction_id != "pet_wave":
        raise ValueError("WorldSpec interaction is not supported")
    if spec.build_slot_id != "small_shelter":
        raise ValueError("WorldSpec build slot is not supported")
    if spec.forbidden_objects != ["vehicle"]:
        raise ValueError("WorldSpec forbidden object contract does not match")
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
    """从最终 PNG 实测尺寸、通道与 SHA-256，生成唯一资产清单。"""
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
        if not path.is_file():
            raise ValueError("asset file is missing: " + asset_id)
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                raise ValueError("asset is not PNG: " + asset_id)
            width, height = image.size
            channels = len(image.getbands())
        if channels not in {3, 4}:
            raise ValueError("asset must have RGB or RGBA channels: " + asset_id)
        matrix = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if matrix is None:
            raise ValueError("OpenCV could not decode asset PNG: " + asset_id)
        entries.append(
            AssetEntry(
                asset_id=asset_id,
                filename=f"{asset_id}.png",
                uri=f"/assets/{asset_id}.png",
                width=width,
                height=height,
                channels=channels,
                anchor=ASSET_ANCHORS[asset_id],
                sha256=sha256_file(path),
            )
        )
    return AssetManifest(assets=entries)


def build_snapshot(plan: ScenePlan, manifest: AssetManifest, spec: WorldSpec) -> SceneSnapshot:
    """仅从已校验的计划、清单和意图组装 Unity 跨界 Snapshot。"""
    declared = {entry.asset_id for entry in manifest.assets}
    for layer in plan.layers:
        if layer.asset_id not in declared:
            raise ValueError("Layer asset is not declared in the manifest: " + layer.asset_id)
    for slot in plan.build_slots:
        for prefab in slot.allowed_prefabs:
            if prefab not in declared:
                raise ValueError("Build slot prefab is not declared in the manifest: " + prefab)

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
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validate(instance=data, schema=schema)


def validate_snapshot(snapshot: SceneSnapshot) -> None:
    validate_snapshot_dict(snapshot.model_dump())
