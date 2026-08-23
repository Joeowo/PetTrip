"""会话 1 的 HTTP DTO。"""

from __future__ import annotations

from typing import Annotated, Literal, Union

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


class ClarificationSubmitInputCommand(BaseModel):
    """提交澄清输入命令。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["clarification.submit_input"]
    input_id: str = Field(min_length=1)
    text: str


class ClarificationCloseCommand(BaseModel):
    """独立关闭澄清命令。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["clarification.close"]
    close_request_id: str = Field(min_length=1)


RunCommand = Annotated[
    Union[ClarificationSubmitInputCommand, ClarificationCloseCommand],
    Field(discriminator="type"),
]


class CreateRunRequest(BaseModel):
    """创建纯文本异步 Run 的请求。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    input: TextInput | None = None
    response_format: TextResponseFormat | None = None
    command: RunCommand | None = None

    @model_validator(mode="after")
    def validate_command_or_input(self) -> "CreateRunRequest":
        """命令模式和传统输入模式互斥。"""
        has_command = self.command is not None
        has_input = self.input is not None

        if has_command and has_input:
            raise ValueError("command 和 input 不能同时提供。")

        if not has_command and not has_input:
            raise ValueError("必须提供 command 或 input。")

        # 传统模式必须提供 response_format
        if has_input and self.response_format is None:
            raise ValueError("使用 input 时必须提供 response_format。")

        # 命令模式不应该提供 response_format
        if has_command and self.response_format is not None:
            raise ValueError("使用 command 时不应提供 response_format。")

        return self


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
