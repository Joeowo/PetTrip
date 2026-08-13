"""会话 1 的 HTTP DTO。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class TextResponseFormat(BaseModel):
    """会话 1 仅接受文本输出。"""

    model_config = ConfigDict(extra="forbid")

    modalities: list[Literal["text"]] = Field(min_length=1, max_length=1)

    def is_text_only(self) -> bool:
        return self.modalities == ["text"]


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
