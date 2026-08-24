"""版本化结构化输出注册表与服务端 JSON Schema 校验。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator


class StructuredOutputInvalid(RuntimeError):
    """请求的 Schema 不受支持，或模型结果未通过服务端校验。"""


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SceneDraftV01(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["scene_draft"]
    schema_version: Literal["0.1"]
    title: NonEmptyText
    theme: NonEmptyText
    summary: NonEmptyText
    landmark_kind: NonEmptyText


class ClarificationTurnV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["clarification_turn"]
    schema_version: Literal["1.0"]
    classification: Literal["empty", "accepted_wish_input", "off_topic", "unintelligible"]
    normalized_text: NonEmptyText | None
    assistant_reply: NonEmptyText
    captured_facts: list[NonEmptyText] = Field(default_factory=list)
    missing_dimensions: list[NonEmptyText] = Field(default_factory=list)
    close_recommendation: bool = False

    @model_validator(mode="after")
    def validate_normalized_text(self) -> "ClarificationTurnV10":
        if self.classification == "accepted_wish_input" and self.normalized_text is None:
            raise ValueError("accepted_wish_input 必须有 normalized_text")
        return self


class RequirementItemV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalized_statement: NonEmptyText
    polarity: Literal["include", "exclude"]
    fulfillment: Literal["must_satisfy", "best_effort", "creative_discretion"]
    source_type: Literal["player_input", "agent_inference", "template_default"]
    source_input_ids: list[str]
    rationale: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "RequirementItemV10":
        if self.source_type == "player_input" and not self.source_input_ids:
            raise ValueError("player_input 必须引用 source_input_ids")
        if self.source_type == "agent_inference" and self.rationale is None:
            raise ValueError("agent_inference 必须有 rationale")
        if self.source_type == "template_default" and self.source_input_ids:
            raise ValueError("template_default 不能引用玩家输入")
        return self


class DestinationRequirementsV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["destination_requirements"]
    schema_version: Literal["1.0"]
    items: list[RequirementItemV10] = Field(min_length=1)


class EnvironmentTemplateSelectionV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["environment_template_selection"]
    schema_version: Literal["1.0"]
    style_template_id: NonEmptyText
    composition_template_id: NonEmptyText
    rationale: NonEmptyText


class VisualAnchorV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor_id: NonEmptyText
    label: NonEmptyText
    landmark: NonEmptyText
    interaction_affordance: NonEmptyText
    placement_guidance: NonEmptyText
    pet_activity: NonEmptyText


class SharedEnvironmentV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: NonEmptyText
    style_constraints: list[NonEmptyText]
    composition_constraints: list[NonEmptyText]
    negative_constraints: list[NonEmptyText]
    visual_anchors: list[VisualAnchorV10] = Field(min_length=2)


class PetIdentityV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_key: Literal["pet/chongwu-bottom.png"]
    role: Literal["canonical_pet_identity"]
    description: NonEmptyText


class ScenePlanV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pet_identity: PetIdentityV10
    visual_anchor_id: NonEmptyText
    order: Literal[0, 1]
    state_label: NonEmptyText
    pet_behavior: NonEmptyText
    pet_emotion: NonEmptyText
    semantic_anchor: NonEmptyText
    interaction_prompt: NonEmptyText


class LocatorSelectionV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["locator_selection"]
    schema_version: Literal["1.0"]
    center_x: int = Field(ge=0)
    center_y: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    grounding: NonEmptyText


class DestinationSpecV10(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["destination_spec"]
    schema_version: Literal["1.0"]
    template_id: NonEmptyText = "default_pet_destination"
    template_version: NonEmptyText = "1.0"
    title: NonEmptyText
    shared_environment_spec: SharedEnvironmentV10
    scene_plans: list[ScenePlanV10] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_plans(self) -> "DestinationSpecV10":
        if {plan.order for plan in self.scene_plans} != {0, 1}:
            raise ValueError("ScenePlan order 必须正好为 0 和 1")
        first, second = sorted(self.scene_plans, key=lambda plan: plan.order)
        if (
            first.state_label == second.state_label
            and first.pet_behavior == second.pet_behavior
            and first.pet_emotion == second.pet_emotion
        ):
            raise ValueError("两个 ScenePlan 必须具有不同状态或行为")
        return self


@dataclass(frozen=True)
class StructuredOutputRequest:
    schema_name: str
    schema_version: str
    json_schema: dict[str, Any]


@dataclass(frozen=True)
class _RegisteredSchema:
    json_schema: dict[str, Any]
    dto_type: type[BaseModel]


class StructuredOutputRegistry:
    """只按 ``schema_name + schema_version`` 暴露显式注册的契约。"""

    def __init__(self) -> None:
        dto_types: dict[tuple[str, str], type[BaseModel]] = {
            ("scene_draft", "0.1"): SceneDraftV01,
            ("clarification_turn", "1.0"): ClarificationTurnV10,
            ("destination_requirements", "1.0"): DestinationRequirementsV10,
            ("environment_template_selection", "1.0"): EnvironmentTemplateSelectionV10,
            ("locator_selection", "1.0"): LocatorSelectionV10,
            ("destination_spec", "1.0"): DestinationSpecV10,
        }
        self._schemas = {
            key: _RegisteredSchema(json_schema=dto.model_json_schema(), dto_type=dto)
            for key, dto in dto_types.items()
        }

    def request_for(self, *, schema_name: str, schema_version: str) -> StructuredOutputRequest:
        registered = self._schemas.get((schema_name, schema_version))
        if registered is None:
            raise StructuredOutputInvalid("不支持请求的结构化输出 Schema。")
        return StructuredOutputRequest(
            schema_name=schema_name,
            schema_version=schema_version,
            json_schema=deepcopy(registered.json_schema),
        )

    def parse_and_validate(
        self, raw_result: str, *, schema_name: str, schema_version: str
    ) -> dict[str, Any]:
        registered = self._schemas.get((schema_name, schema_version))
        if registered is None:
            raise StructuredOutputInvalid("不支持请求的结构化输出 Schema。")
        try:
            parsed = json.loads(raw_result)
        except (json.JSONDecodeError, TypeError) as exc:
            raise StructuredOutputInvalid("结构化输出不是合法 JSON。") from exc
        errors = list(Draft202012Validator(registered.json_schema).iter_errors(parsed))
        if errors:
            raise StructuredOutputInvalid("结构化输出未通过 JSON Schema 校验。")
        try:
            dto = registered.dto_type.model_validate(parsed)
        except ValidationError as exc:
            raise StructuredOutputInvalid("结构化输出未通过固定 DTO 校验。") from exc
        return dto.model_dump(mode="json")
