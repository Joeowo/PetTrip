"""会话3 严格中间契约与结构化输出结果。"""

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
