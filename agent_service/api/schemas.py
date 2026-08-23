"""会话 1 的 HTTP DTO。"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator, Discriminator


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


# ---- Run Command Union Type (Issue #10 Section 5) -------------------------

class ClarificationSubmitInputCommand(BaseModel):
    """澄清流程提交玩家输入命令。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["clarification.submit_input"] = "clarification.submit_input"
    input_id: str = Field(min_length=1, description="客户端稳定的输入标识")
    text: str = Field(description="玩家输入文本，允许空字符串")


class ClarificationCloseCommand(BaseModel):
    """Unity 主动关闭澄清流程命令。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["clarification.close"] = "clarification.close"
    close_request_id: str = Field(min_length=1, description="客户端稳定的关闭请求标识")


class AgentGenerateCommand(BaseModel):
    """传统 Agent 生成命令（向后兼容）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["agent.generate"] = "agent.generate"


RunCommand = Annotated[
    Union[
        ClarificationSubmitInputCommand,
        ClarificationCloseCommand,
        AgentGenerateCommand,
    ],
    Discriminator("type"),
]


class CreateRunRequest(BaseModel):
    """创建纯文本异步 Run 的请求。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    command: RunCommand | None = None  # None 表示向后兼容的隐式 agent.generate
    input: TextInput | None = None  # command 存在时可选
    response_format: TextResponseFormat | None = None  # command 存在时可选

    @model_validator(mode="after")
    def validate_command_constraints(self) -> "CreateRunRequest":
        """验证命令约束条件。"""
        if self.command is None:
            # 向后兼容模式：必须提供 input 和 response_format
            if self.input is None or self.response_format is None:
                raise ValueError("未提供 command 时必须提供 input 和 response_format。")
        else:
            # 命令模式
            if isinstance(self.command, ClarificationSubmitInputCommand):
                # 提交输入命令：text 在 command 中，不能同时提供独立的 input
                if self.input is not None:
                    raise ValueError(
                        "clarification.submit_input 命令的文本已在 command 中，不能同时提供 input。"
                    )
            elif isinstance(self.command, ClarificationCloseCommand):
                # 关闭命令：不能携带文本
                if self.input is not None and self.input.text:
                    raise ValueError(
                        "clarification.close 命令不能携带玩家文本输入。"
                    )
        return self


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
