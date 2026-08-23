"""会话4 契约模型：SceneSnapshot v0.2（槽位放置）、统一输入与 Unity 验证报告。

相对会话3 models.py 的差异：
- BuildSlot 增加可选 placed_prefab，配合 contracts/scene-snapshot/v0.2.schema.json；
  省略表示槽位未放置，"small_shelter" 表示已放置。
- schema_version 允许 0.1（会话1-3 历史产物）与 0.2（会话4 起）。
- 新增统一输入（RunRequest）与 Unity 验证报告（UnityReport）DTO，均禁止额外字段。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorldSpec(BaseModel):
    """模型必须完整返回的固定海边灯塔意图；禁止默认补值和额外字段。"""

    model_config = ConfigDict(extra="forbid")

    theme: Literal["seaside"]
    landmark: Literal["lighthouse"]
    interaction_id: Literal["pet_wave"]
    build_slot_id: Literal["small_shelter"]
    forbidden_objects: list[Literal["vehicle"]] = Field(min_length=1, max_length=1)
    canvas_width: Literal[512]
    canvas_height: Literal[288]
    pixels_per_unit: Literal[16]


class Point(BaseModel):
    x: float
    y: float


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


class AssetEntry(BaseModel):
    asset_id: str
    kind: Literal["sprite"] = "sprite"
    filename: str
    uri: str
    mime_type: Literal["image/png"] = "image/png"
    width: int
    height: int
    channels: Literal[3, 4]
    anchor: Point
    sha256: str


class AssetManifest(BaseModel):
    schema_version: Literal["0.1"] = "0.1"
    assets: list[AssetEntry]


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
    placed_prefab: str | None = None


class SceneSnapshot(BaseModel):
    schema_version: str
    scene_id: str
    canvas: Canvas
    layers: list[Layer]
    activity_zone: ActivityZone
    interactions: list[Interaction]
    build_slots: list[BuildSlot]


class RunRequest(BaseModel):
    """统一输入：显式 run_id 与场景输入文本。缺任一字段由 FastAPI 返回 422。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    input: str = Field(min_length=1)


class ReportCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class UnityReport(BaseModel):
    """Unity 验证报告：run_id 与 snapshot 哈希必须与服务端当前快照一致。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checks: list[ReportCheck] = Field(min_length=1)
    screenshot_png_base64: str = Field(min_length=1)
