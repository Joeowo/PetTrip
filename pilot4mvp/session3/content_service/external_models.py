"""Responses 优先的结构化 WorldSpec Provider，含显式兼容适配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from .config import ProviderConfig
from .models import ProviderFailure, StructuredOutputEvidence, WorldSpec

SCENE_PROMPT = (
    "生成一个横向 2D 海边场景，包含一座灯塔；宠物可以在灯塔前挥手；"
    "右侧可以放置一个小窝；不要出现车辆。请严格按提供的 JSON Schema 返回。"
)
UNSUPPORTED_MARKERS = (
    "unknown endpoint",
    "unsupported endpoint",
    "endpoint not found",
    "not implemented",
    "unsupported parameter: text.format",
    "unknown parameter: text.format",
    "does not support structured outputs",
    "structured outputs are not supported",
)
MODEL_MARKERS = (
    "model not found",
    "unknown model",
    "unsupported model",
    "model_not_found",
    "does not exist",
    "no access to model",
)
POLICY_MARKERS = ("content policy", "safety", "policy violation", "refusal", "moderation")


@dataclass(frozen=True)
class CallRecord:
    endpoint: str
    method: str
    request: dict[str, Any]
    response: Any
    http_status: int | None
    request_id: str | None


class ProviderCallError(RuntimeError):
    """带稳定分类和完整脱敏前调用轨迹的 Provider 失败。"""

    def __init__(
        self,
        failure: ProviderFailure,
        response_data: Any = None,
        calls: dict[str, CallRecord] | None = None,
    ) -> None:
        super().__init__(failure.message)
        self.failure = failure
        self.response_data = response_data
        self.calls = calls or {}


@dataclass(frozen=True)
class StructuredOutputResult:
    world_spec: WorldSpec
    evidence: StructuredOutputEvidence
    requests: dict[str, dict[str, Any]]
    responses: dict[str, Any]
    calls: dict[str, CallRecord]


def resolve_endpoint(base_url: str, suffix: str) -> str:
    """将带或不带 /v1 的 Base URL 解析为一个明确 API 端点。"""
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base_url must be an absolute http(s) URL")
    base_path = parts.path.rstrip("/")
    if not base_path.endswith("/v1"):
        base_path += "/v1"
    path = base_path + "/" + suffix.lstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _request_id(response: httpx.Response) -> str | None:
    return response.headers.get("x-request-id") or response.headers.get("request-id")


def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text}


def _error_text(data: Any) -> str:
    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict):
            return " ".join(
                str(error.get(key, "")) for key in ("code", "type", "message")
            ).strip()
    return str(data)


def _category(status: int, data: Any) -> str:
    text = _error_text(data).lower()
    if any(marker in text for marker in POLICY_MARKERS):
        return "policy"
    if status in {401, 403}:
        return "authentication"
    if status in {408, 504}:
        return "timeout"
    if any(marker in text for marker in MODEL_MARKERS):
        return "model"
    if status in {404, 405} or status >= 500:
        return "endpoint"
    return "endpoint"


def _compatibility_allowed(response: httpx.Response, data: Any) -> bool:
    text = _error_text(data).lower()
    if any(marker in text for marker in MODEL_MARKERS + POLICY_MARKERS):
        return False
    if response.status_code == 405:
        return True
    if response.status_code == 404:
        return True
    if response.status_code not in {400, 422}:
        return False
    return any(marker in text for marker in UNSUPPORTED_MARKERS)


def _failure(
    *,
    stage: str,
    category: str,
    message: str,
    endpoint: str,
    model: str,
    response: httpx.Response | None = None,
) -> ProviderFailure:
    return ProviderFailure(
        stage=stage,
        category=category,
        message=message,
        endpoint=endpoint,
        model=model,
        http_status=response.status_code if response is not None else None,
        request_id=_request_id(response) if response is not None else None,
    )


def _parse_world_spec(
    text: str,
    endpoint: str,
    model: str,
    response: httpx.Response,
    response_data: Any,
) -> WorldSpec:
    try:
        return WorldSpec.model_validate_json(text)
    except ValidationError as exc:
        raise ProviderCallError(
            _failure(
                stage="world_spec",
                category="decode",
                message="structured output did not directly validate as WorldSpec: " + str(exc),
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            response_data,
        ) from exc


def _responses_text(data: Any, endpoint: str, model: str, response: httpx.Response) -> str:
    if not isinstance(data, dict):
        raise ProviderCallError(
            _failure(
                stage="responses",
                category="decode",
                message="Responses body is not a JSON object",
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            data,
        )
    status = data.get("status")
    if status not in {None, "completed"}:
        details = str(data.get("incomplete_details") or "").lower()
        if data.get("error") is not None:
            category = _category(response.status_code, data)
        elif any(marker in details for marker in POLICY_MARKERS + ("content_filter",)):
            category = "policy"
        else:
            category = "decode"
        raise ProviderCallError(
            _failure(
                stage="responses",
                category=category,
                message=f"Responses result is not completed: {status}",
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            data,
        )
    aggregate_text = data.get("output_text")
    texts: list[str] = []
    refusals: list[str] = []
    for output in data.get("output", []) if isinstance(data.get("output"), list) else []:
        if not isinstance(output, dict):
            continue
        for item in output.get("content", []) if isinstance(output.get("content"), list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "output_text" and isinstance(item.get("text"), str):
                texts.append(item["text"])
            if item.get("type") == "refusal" or isinstance(item.get("refusal"), str):
                refusals.append(str(item.get("refusal") or item.get("text") or "refused"))
    if refusals:
        raise ProviderCallError(
            _failure(
                stage="responses",
                category="policy",
                message="Responses refused the request",
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            data,
        )
    if isinstance(aggregate_text, str):
        return aggregate_text
    if not texts:
        raise ProviderCallError(
            _failure(
                stage="responses",
                category="decode",
                message="Responses result has no output text",
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            data,
        )
    return "".join(texts)


def _chat_text(data: Any, endpoint: str, model: str, response: httpx.Response) -> str:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderCallError(
            _failure(
                stage="chat_completions_compat",
                category="decode",
                message="Chat Completions response has no first message",
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            data,
        ) from exc
    if not isinstance(message, dict):
        raise ProviderCallError(
            _failure(
                stage="chat_completions_compat",
                category="decode",
                message="Chat Completions first message is not a JSON object",
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            data,
        )
    if message.get("refusal"):
        raise ProviderCallError(
            _failure(
                stage="chat_completions_compat",
                category="policy",
                message="Chat Completions refused the request",
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            data,
        )
    content = message.get("content")
    if not isinstance(content, str):
        raise ProviderCallError(
            _failure(
                stage="chat_completions_compat",
                category="decode",
                message="Chat Completions message content is not text",
                endpoint=endpoint,
                model=model,
                response=response,
            ),
            data,
        )
    return content


class StructuredOutputProvider:
    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
        allow_chat_compat: bool = False,
    ) -> None:
        self.config = config
        self.transport = transport
        self.timeout = timeout
        self.allow_chat_compat = allow_chat_compat

    def _post(self, endpoint: str, payload: dict[str, Any], stage: str) -> httpx.Response:
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                return client.post(
                    endpoint,
                    headers={
                        "Authorization": "Bearer " + self.config.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderCallError(
                _failure(
                    stage=stage,
                    category="timeout",
                    message=f"{stage} request timed out",
                    endpoint=endpoint,
                    model=self.config.model,
                )
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderCallError(
                _failure(
                    stage=stage,
                    category="endpoint",
                    message=f"{stage} request failed before receiving a response: {type(exc).__name__}",
                    endpoint=endpoint,
                    model=self.config.model,
                )
            ) from exc

    def generate_world_spec(self, prompt: str = SCENE_PROMPT) -> StructuredOutputResult:
        schema = WorldSpec.model_json_schema()
        try:
            responses_endpoint = resolve_endpoint(self.config.base_url, "responses")
        except ValueError as exc:
            raise ProviderCallError(
                _failure(
                    stage="responses",
                    category="endpoint",
                    message=str(exc),
                    endpoint=self.config.base_url,
                    model=self.config.model,
                )
            ) from exc
        responses_payload = {
            "model": self.config.model,
            "input": prompt,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "pettrip_world_spec",
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        requests: dict[str, dict[str, Any]] = {"responses": responses_payload}
        responses: dict[str, Any] = {}
        calls: dict[str, CallRecord] = {}
        try:
            response = self._post(responses_endpoint, responses_payload, "responses")
        except ProviderCallError as exc:
            exc.calls = {
                "responses": CallRecord(
                    endpoint=responses_endpoint,
                    method="POST",
                    request=responses_payload,
                    response=exc.response_data,
                    http_status=exc.failure.http_status,
                    request_id=exc.failure.request_id,
                )
            }
            raise
        data = _response_json(response)
        responses["responses"] = data
        calls["responses"] = CallRecord(
            endpoint=responses_endpoint,
            method="POST",
            request=responses_payload,
            response=data,
            http_status=response.status_code,
            request_id=_request_id(response),
        )

        if response.is_success:
            try:
                text = _responses_text(data, responses_endpoint, self.config.model, response)
                spec = _parse_world_spec(
                    text, responses_endpoint, self.config.model, response, data
                )
            except ProviderCallError as exc:
                exc.calls = calls
                raise
            return StructuredOutputResult(
                world_spec=spec,
                evidence=StructuredOutputEvidence(
                    structured_output_api="responses",
                    responses_attempted=True,
                    responses_passed=True,
                    compatibility_adapter_allowed=self.allow_chat_compat,
                    compatibility_adapter_used=False,
                ),
                requests=requests,
                responses=responses,
                calls=calls,
            )

        response_failure = _failure(
            stage="responses",
            category=_category(response.status_code, data),
            message="Responses request failed: " + _error_text(data),
            endpoint=responses_endpoint,
            model=self.config.model,
            response=response,
        )
        if not self.allow_chat_compat or not _compatibility_allowed(response, data):
            raise ProviderCallError(response_failure, data, calls)

        chat_endpoint = resolve_endpoint(self.config.base_url, "chat/completions")
        chat_payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "pettrip_world_spec",
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        requests["chat_completions_compat"] = chat_payload
        try:
            chat_response = self._post(chat_endpoint, chat_payload, "chat_completions_compat")
        except ProviderCallError as exc:
            calls["chat_completions_compat"] = CallRecord(
                endpoint=chat_endpoint,
                method="POST",
                request=chat_payload,
                response=exc.response_data,
                http_status=exc.failure.http_status,
                request_id=exc.failure.request_id,
            )
            exc.calls = calls
            raise
        chat_data = _response_json(chat_response)
        responses["chat_completions_compat"] = chat_data
        calls["chat_completions_compat"] = CallRecord(
            endpoint=chat_endpoint,
            method="POST",
            request=chat_payload,
            response=chat_data,
            http_status=chat_response.status_code,
            request_id=_request_id(chat_response),
        )
        if not chat_response.is_success:
            raise ProviderCallError(
                _failure(
                    stage="chat_completions_compat",
                    category=_category(chat_response.status_code, chat_data),
                    message="Chat Completions compatibility request failed: " + _error_text(chat_data),
                    endpoint=chat_endpoint,
                    model=self.config.model,
                    response=chat_response,
                ),
                chat_data,
                calls,
            )
        try:
            chat_text = _chat_text(chat_data, chat_endpoint, self.config.model, chat_response)
            spec = _parse_world_spec(
                chat_text, chat_endpoint, self.config.model, chat_response, chat_data
            )
        except ProviderCallError as exc:
            exc.calls = calls
            raise
        return StructuredOutputResult(
            world_spec=spec,
            evidence=StructuredOutputEvidence(
                structured_output_api="chat_completions_compat",
                responses_attempted=True,
                responses_passed=False,
                compatibility_adapter_allowed=True,
                compatibility_adapter_used=True,
                responses_failure=response_failure,
            ),
            requests=requests,
            responses=responses,
            calls=calls,
        )
