from __future__ import annotations

from agent_service.shared.config import load_settings


def test_early_responses_configuration_falls_back_to_chat_settings() -> None:
    settings = load_settings(
        overrides={
            "PILOT_API_KEY": "pilot-key",
            "RESPONSES_BASE_URL": "https://chat.example/v1",
            "RESPONSES_API_KEY": "chat-key",
            "RESPONSES_MODEL": "chat-model",
        }
    )

    assert settings.chat_base_url == "https://chat.example/v1"
    assert settings.chat_api_key == "chat-key"
    assert settings.chat_model == "chat-model"


def test_explicit_chat_configuration_wins_over_early_responses_configuration() -> None:
    settings = load_settings(
        overrides={
            "PILOT_API_KEY": "pilot-key",
            "CHAT_BASE_URL": "https://chat.example/v1",
            "CHAT_API_KEY": "chat-key",
            "CHAT_MODEL": "chat-model",
            "RESPONSES_BASE_URL": "https://legacy.example/v1",
            "RESPONSES_API_KEY": "legacy-key",
            "RESPONSES_MODEL": "legacy-model",
        }
    )

    assert settings.chat_base_url == "https://chat.example/v1"
    assert settings.chat_api_key == "chat-key"
    assert settings.chat_model == "chat-model"
