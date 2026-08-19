"""会话 1 的 HTTP DTO。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputAttachment(BaseModel):
    """客户端通过本地文件资源 ID 引用一张图片。"""

    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(min_length=1)
    purpose: Literal["vision_input", "reference_image"]


class TextInput(BaseModel):
    """文本与可选图片附件输入。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    attachments: list[InputAttachment] = Field(default_factory=list, max_length=4)


class StructuredOutputFormat(BaseModel):
    """用名称和版本定位一次结构化输出请求。"""

    model_config = ConfigDict(extra="forbid")

    schema_name: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)


class TextResponseFormat(BaseModel):
    """声明本次 Run 需要的输出模态及可选结构化 Schema。"""

    model_config = ConfigDict(extra="forbid")

    modalities: list[Literal["text", "structured_data", "image"]] = Field(
        min_length=1, max_length=3
    )
    structured_output: StructuredOutputFormat | None = None

    def is_text_only(self) -> bool:
        return self.modalities == ["text"]

    def wants_image(self) -> bool:
        return "image" in self.modalities

    def wants_text(self) -> bool:
        return "text" in self.modalities

    def wants_structured_data(self) -> bool:
        return "structured_data" in self.modalities

    @model_validator(mode="after")
    def require_matching_structured_output(self) -> "TextResponseFormat":
        wants_structured = self.wants_structured_data()
        if wants_structured != (self.structured_output is not None):
            raise ValueError(
                "structured_data 模态和 structured_output 必须同时提供。"
            )
        return self


class CreateRunRequest(BaseModel):
    """创建纯文本异步 Run 的请求。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    input: TextInput
    response_format: TextResponseFormat


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
