"""会话3 严格中间契约、资产证据与 SceneSnapshot DTO。"""

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


class ProviderFailure(BaseModel):
    """可落盘的稳定失败结构，不包含凭证或请求头。"""

    model_config = ConfigDict(extra="forbid")

    stage: str
    category: Literal["authentication", "endpoint", "model", "policy", "timeout", "decode"]
    message: str
    endpoint: str
    model: str
    http_status: int | None = None
    request_id: str | None = None


class StructuredOutputEvidence(BaseModel):
    """结构化输出实际调用路径和兼容边界。"""

    model_config = ConfigDict(extra="forbid")

    structured_output_api: Literal["responses", "chat_completions_compat"]
    responses_attempted: Literal[True]
    responses_passed: bool
    compatibility_adapter_allowed: bool
    compatibility_adapter_used: bool
    responses_failure: ProviderFailure | None = None


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


class ImageArtifact(BaseModel):
    asset_id: Literal["beach_background"]
    source: Literal["images_api"] = "images_api"
    model: str
    raw_filename: str
    filename: str
    uri: str
    mime_type: Literal["image/png"] = "image/png"
    original_width: int
    original_height: int
    normalized_width: Literal[512]
    normalized_height: Literal[288]
    channels: Literal[3, 4]
    raw_sha256: str
    sha256: str


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
