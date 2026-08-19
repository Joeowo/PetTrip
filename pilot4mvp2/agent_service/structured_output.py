"""版本化结构化输出注册表与服务端 JSON Schema 校验。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError


class StructuredOutputInvalid(RuntimeError):
    """请求的 Schema 不受支持，或模型结果未通过服务端校验。"""


NonEmptyText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r".*\S.*",
    ),
]


class SceneDraftV01(BaseModel):
    """服务端 `scene_draft` `0.1` 的固定持久化 DTO。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["scene_draft"]
    schema_version: Literal["0.1"]
    title: NonEmptyText
    theme: NonEmptyText
    summary: NonEmptyText
    landmark_kind: NonEmptyText


SCENE_DRAFT_V01_SCHEMA = SceneDraftV01.model_json_schema()


@dataclass(frozen=True)
class StructuredOutputRequest:
    """传给 Chat Provider 的已注册 Schema 请求。"""

    schema_name: str
    schema_version: str
    json_schema: dict[str, Any]


@dataclass(frozen=True)
class _RegisteredSchema:
    json_schema: dict[str, Any]
    dto_type: type[BaseModel]


class StructuredOutputRegistry:
    """只按 `schema_name + schema_version` 暴露显式注册的契约。"""

    def __init__(self) -> None:
        self._schemas = {
            ("scene_draft", "0.1"): _RegisteredSchema(
                json_schema=SCENE_DRAFT_V01_SCHEMA,
                dto_type=SceneDraftV01,
            )
        }

    def request_for(
        self, *, schema_name: str, schema_version: str
    ) -> StructuredOutputRequest:
        registered = self._schemas.get((schema_name, schema_version))
        if registered is None:
            raise StructuredOutputInvalid("不支持请求的结构化输出 Schema。")
        return StructuredOutputRequest(
            schema_name=schema_name,
            schema_version=schema_version,
            json_schema=deepcopy(registered.json_schema),
        )

    def parse_and_validate(
        self,
        raw_result: str,
        *,
        schema_name: str,
        schema_version: str,
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
