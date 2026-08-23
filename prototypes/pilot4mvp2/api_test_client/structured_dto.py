"""会话 4 API 测试客户端的固定结构化输出 DTO。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints


NonEmptyText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r".*\S.*",
    ),
]


class SceneDraftDtoV01(BaseModel):
    """客户端只接受 `scene_draft` `0.1` 的固定字段。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["scene_draft"]
    schema_version: Literal["0.1"]
    title: NonEmptyText
    theme: NonEmptyText
    summary: NonEmptyText
    landmark_kind: NonEmptyText


def read_scene_draft_v01(run_response: dict[str, Any]) -> SceneDraftDtoV01:
    """只读取专用字段；绝不从助手文本扫描或提取 JSON。"""
    output = run_response.get("output")
    if not isinstance(output, dict) or "structured_data" not in output:
        raise ValueError("响应缺少 output.structured_data。")
    return SceneDraftDtoV01.model_validate(output["structured_data"])
