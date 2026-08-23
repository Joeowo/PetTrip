from __future__ import annotations

import json

from content_service.evidence import credential_hits, provider_response_evidence, write_json


def test_write_json_redacts_key_headers_and_url_query(tmp_path) -> None:
    api_key = "test-secret-key"
    path = tmp_path / "evidence.json"
    write_json(
        path,
        {
            "authorization": "Bearer " + api_key,
            "nested": {"api_key": api_key, "message": "value=" + api_key},
            "endpoint": "https://user:password@example.test/v1/responses?debug=" + api_key,
            "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        },
        api_key,
    )

    text = path.read_text(encoding="utf-8")
    assert api_key not in text
    data = json.loads(text)
    assert data["authorization"] == "<redacted>"
    assert data["nested"]["api_key"] == "<redacted>"
    assert data["endpoint"] == "https://example.test/v1/responses"
    assert data["usage"] == {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}


def test_provider_response_evidence_drops_internal_fields() -> None:
    projected = provider_response_evidence(
        {
            "id": "resp_123",
            "status": "completed",
            "model": "test-model",
            "instructions": "internal upstream instructions",
            "prompt_cache_key": "private-cache-key",
            "safety_identifier": "private-safety-id",
            "output": [{"type": "message", "content": []}],
            "usage": {"total_tokens": 10},
        },
        "request-123",
    )

    assert projected == {
        "id": "resp_123",
        "status": "completed",
        "model": "test-model",
        "output": [{"type": "message", "content": []}],
        "usage": {"total_tokens": 10},
        "request_id": "request-123",
    }


def test_credential_hits_returns_only_relative_file_names(tmp_path) -> None:
    secret = "full-secret-value"
    (tmp_path / "safe.json").write_text("{}", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "unsafe.bin").write_bytes(b"prefix-" + secret.encode() + b"-suffix")

    assert credential_hits(tmp_path, (secret, "")) == ["nested/unsafe.bin"]
