from __future__ import annotations

import pytest

from content_service.config import ProviderConfig, RESPONSES_VARIABLES, missing_variables, responses_config


def test_provider_repr_hides_key() -> None:
    config = ProviderConfig("https://example.test", "visible-only-to-code", "model")
    assert "visible-only-to-code" not in repr(config)
    assert "<hidden>" in repr(config)


def test_missing_variables_reports_names_only(monkeypatch) -> None:
    for name in RESPONSES_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESPONSES_API_KEY", "secret")
    assert missing_variables(RESPONSES_VARIABLES) == ["RESPONSES_BASE_URL", "RESPONSES_MODEL"]


def test_responses_config_fails_without_printing_values(monkeypatch) -> None:
    for name in RESPONSES_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESPONSES_API_KEY", "secret-value")
    with pytest.raises(RuntimeError) as caught:
        responses_config()
    assert "secret-value" not in str(caught.value)
