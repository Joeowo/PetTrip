"""PetTrip 会话2 内容服务数据模型。

链路: WorldSpec -> ScenePlan -> AssetManifest -> SceneSnapshot。
SceneSnapshot 是唯一跨 Unity 的内容边界，字段必须符合
contracts/scene-snapshot/v0.1.schema.json。WorldSpec/ScenePlan/AssetManifest
是会话2的中间产物与证据，结构保持最小。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# --- 中间产物: 玩家世界意图 (会话3 由 OpenAI Responses 生成，会话2 用固定值) ---
class WorldSpec(BaseModel):
    theme: Literal["seaside"] = "seaside"
    landmark: Literal["lighthouse"] = "lighthouse"
    canvas_width: int = 512
    canvas_height: int = 288
    pixels_per_unit: int = 16


# --- 中间产物: 场景规划 ---
class LayerPlan(BaseModel):
    id: str
    asset_id: str
    sorting_order: int
    x: float
    y: float


class ActivityZonePlan(BaseModel):
    id: str
    type: str
    points: list[tuple[float, float]]


class InteractionPlan(BaseModel):
    id: str
    kind: str
    x: float
    y: float
    radius: float


class BuildSlotPlan(BaseModel):
    id: str
    x: float
    y: float
    allowed_prefabs: list[str]


class ScenePlan(BaseModel):
    scene_id: str
    layers: list[LayerPlan]
    activity_zone: ActivityZonePlan
    interactions: list[InteractionPlan]
    build_slots: list[BuildSlotPlan]


# --- 中间产物: 资产清单 (asset_id 经约定 URI 提供，不含绝对路径) ---
class AssetEntry(BaseModel):
    asset_id: str
    kind: Literal["sprite"] = "sprite"
    filename: str
    width: int
    height: int


class AssetManifest(BaseModel):
    schema_version: Literal["0.1"] = "0.1"
    assets: list[AssetEntry]


# --- 最终交付: SceneSnapshot (字段对齐 contracts JSON Schema) ---
class Point(BaseModel):
    x: float
    y: float


class Canvas(BaseModel):
    width: int
    height: int
    pixels_per_unit: int


class Layer(BaseModel):
    id: str
    asset_id: str
    sorting_order: int
    position: Point


class ActivityZone(BaseModel):
    id: str
    type: str
    points: list[Point]


class Interaction(BaseModel):
    id: str
    kind: str
    anchor: Point
    radius: float


class BuildSlot(BaseModel):
    id: str
    position: Point
    allowed_prefabs: list[str]


class SceneSnapshot(BaseModel):
    schema_version: str
    scene_id: str
    canvas: Canvas
    layers: list[Layer]
    activity_zone: ActivityZone
    interactions: list[Interaction]
    build_slots: list[BuildSlot]
