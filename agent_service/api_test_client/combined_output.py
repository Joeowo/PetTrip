"""会话 5 API 测试客户端的固定组合输出 DTO。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .structured_dto import NonEmptyText, SceneDraftDtoV01


class GeneratedImageDto(BaseModel):
    """客户端接受的生成图片引用。"""

    model_config = ConfigDict(extra="forbid")

    file_id: NonEmptyText
    source: Literal["agent_generated"]
    purpose: Literal["generated_image"]
    mime_type: Literal["image/png"]
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    created_at: NonEmptyText
    download_url: NonEmptyText


class CombinedOutputDto(BaseModel):
    """同一成功 Run 的文本、结构化数据和生成图片。"""

    model_config = ConfigDict(extra="forbid")

    text: NonEmptyText
    structured_data: SceneDraftDtoV01
    attachments: list[GeneratedImageDto] = Field(min_length=1)


def read_combined_output(run_response: dict[str, Any]) -> CombinedOutputDto:
    """严格读取三个专用输出字段，不从文本推断结构化数据。"""
    if run_response.get("status") != "succeeded":
        raise ValueError("组合输出只能从 succeeded Run 读取。")
    output = run_response.get("output")
    if not isinstance(output, dict):
        raise ValueError("响应缺少 output。")
    return CombinedOutputDto.model_validate(output)
