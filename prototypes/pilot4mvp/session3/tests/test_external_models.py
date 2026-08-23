from __future__ import annotations

import json

import httpx
import pytest

from content_service.config import ProviderConfig
from content_service.external_models import (
    ProviderCallError,
    StructuredOutputProvider,
    resolve_endpoint,
)

VALID_WORLD = {
    "theme": "seaside",
    "landmark": "lighthouse",
    "interaction_id": "pet_wave",
    "build_slot_id": "small_shelter",
    "forbidden_objects": ["vehicle"],
    "canvas_width": 512,
    "canvas_height": 288,
    "pixels_per_unit": 16,
}
CONFIG = ProviderConfig("https://gateway.test", "test-key", "test-model")


def _provider(handler, *, allow_chat_compat: bool = True) -> StructuredOutputProvider:
    return StructuredOutputProvider(
        CONFIG,
        transport=httpx.MockTransport(handler),
        allow_chat_compat=allow_chat_compat,
    )


def test_resolve_endpoint_handles_base_url_with_or_without_v1() -> None:
    assert resolve_endpoint("https://gateway.test", "responses") == "https://gateway.test/v1/responses"
    assert resolve_endpoint("https://gateway.test/v1/", "responses") == "https://gateway.test/v1/responses"


def test_responses_success_does_not_call_chat_and_uses_text_format() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        payload = json.loads(request.content)
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        assert payload["store"] is False
        return httpx.Response(200, json={"output_text": json.dumps(VALID_WORLD)})

    result = _provider(handler).generate_world_spec()
    assert paths == ["/v1/responses"]
    assert result.evidence.structured_output_api == "responses"
    assert result.evidence.responses_passed is True


def test_chat_compatibility_is_disabled_by_default() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404, json={"error": {"message": "Not Found"}})

    with pytest.raises(ProviderCallError):
        _provider(handler, allow_chat_compat=False).generate_world_spec()
    assert paths == ["/v1/responses"]


def test_unsupported_responses_falls_back_to_chat_json_schema() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(404, json={"error": {"message": "unknown endpoint"}})
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(VALID_WORLD)}}]},
        )

    result = _provider(handler).generate_world_spec()
    assert paths == ["/v1/responses", "/v1/chat/completions"]
    assert result.evidence.structured_output_api == "chat_completions_compat"
    assert result.evidence.responses_passed is False
    assert result.evidence.compatibility_adapter_used is True
    assert result.evidence.responses_failure.category == "endpoint"


@pytest.mark.parametrize(
    ("status", "error", "category"),
    [
        (401, {"message": "invalid API key"}, "authentication"),
        (400, {"message": "model not found"}, "model"),
        (400, {"message": "content policy refusal"}, "policy"),
    ],
)
def test_non_compatibility_failures_do_not_call_chat(status, error, category) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(status, json={"error": error})

    with pytest.raises(ProviderCallError) as caught:
        _provider(handler).generate_world_spec()
    assert paths == ["/v1/responses"]
    assert caught.value.failure.category == category


def test_invalid_responses_world_spec_does_not_fall_back() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        invalid = dict(VALID_WORLD)
        invalid.pop("interaction_id")
        return httpx.Response(200, json={"output_text": json.dumps(invalid)})

    with pytest.raises(ProviderCallError) as caught:
        _provider(handler).generate_world_spec()
    assert paths == ["/v1/responses"]
    assert caught.value.failure.category == "decode"


def test_404_model_not_found_does_not_fall_back() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404, json={"error": {"message": "model not found"}})

    with pytest.raises(ProviderCallError) as caught:
        _provider(handler).generate_world_spec()
    assert paths == ["/v1/responses"]
    assert caught.value.failure.category == "model"


def test_generic_404_falls_back_after_model_and_policy_are_excluded() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(404, json={"error": {"type": "not_found_error", "message": "Not Found"}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(VALID_WORLD)}}]},
        )

    result = _provider(handler).generate_world_spec()
    assert paths == ["/v1/responses", "/v1/chat/completions"]
    assert result.evidence.compatibility_adapter_used is True


def test_invalid_json_schema_error_does_not_fall_back() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(400, json={"error": {"message": "invalid json_schema"}})

    with pytest.raises(ProviderCallError):
        _provider(handler).generate_world_spec()
    assert paths == ["/v1/responses"]


def test_failed_responses_status_with_text_is_not_success() -> None:
    response = {"status": "failed", "output_text": json.dumps(VALID_WORLD)}

    with pytest.raises(ProviderCallError) as caught:
        _provider(lambda request: httpx.Response(200, json=response)).generate_world_spec()
    assert caught.value.failure.category == "decode"


def test_failed_responses_status_uses_structured_error_category() -> None:
    response = {
        "status": "failed",
        "error": {"code": "model_not_found", "message": "model not found"},
    }

    with pytest.raises(ProviderCallError) as caught:
        _provider(lambda request: httpx.Response(200, json=response)).generate_world_spec()
    assert caught.value.failure.category == "model"


def test_403_policy_error_is_not_misclassified_as_authentication() -> None:
    response = {"error": {"message": "content policy violation"}}

    with pytest.raises(ProviderCallError) as caught:
        _provider(lambda request: httpx.Response(403, json=response)).generate_world_spec()
    assert caught.value.failure.category == "policy"


def test_responses_joins_ordered_output_text_parts() -> None:
    text = json.dumps(VALID_WORLD)
    split = len(text) // 2
    response = {
        "status": "completed",
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": text[:split]},
                    {"type": "output_text", "text": text[split:]},
                ]
            }
        ],
    }

    result = _provider(lambda request: httpx.Response(200, json=response)).generate_world_spec()
    assert result.world_spec.interaction_id == "pet_wave"


def test_responses_refusal_is_policy_failure() -> None:
    response = {
        "output": [{"content": [{"type": "refusal", "refusal": "cannot comply"}]}]
    }

    with pytest.raises(ProviderCallError) as caught:
        _provider(lambda request: httpx.Response(200, json=response)).generate_world_spec()
    assert caught.value.failure.category == "policy"


def test_null_chat_message_is_wrapped_and_keeps_two_call_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/responses"):
            return httpx.Response(404, json={"error": {"message": "Not Found"}})
        return httpx.Response(200, json={"choices": [{"message": None}]})

    with pytest.raises(ProviderCallError) as caught:
        _provider(handler).generate_world_spec()
    assert caught.value.failure.category == "decode"
    assert set(caught.value.calls) == {"responses", "chat_completions_compat"}
    assert caught.value.calls["responses"].http_status == 404
    assert caught.value.calls["chat_completions_compat"].http_status == 200


def test_invalid_base_url_is_classified_as_endpoint() -> None:
    provider = StructuredOutputProvider(
        ProviderConfig("gateway.test", "test-key", "test-model")
    )
    with pytest.raises(ProviderCallError) as caught:
        provider.generate_world_spec()
    assert caught.value.failure.category == "endpoint"


def test_timeout_is_classified_without_chat_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderCallError) as caught:
        _provider(handler).generate_world_spec()
    assert caught.value.failure.category == "timeout"
