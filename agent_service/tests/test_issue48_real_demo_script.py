from __future__ import annotations

import sys

from agent_service.scripts import run_real_provider_demo


def test_real_demo_requires_explicit_service_configuration(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PETTRIP_BASE_URL", raising=False)
    monkeypatch.delenv("PETTRIP_API_KEY", raising=False)
    assert run_real_provider_demo.main() == 2
    assert "PETTRIP_BASE_URL" in capsys.readouterr().err


def test_real_demo_rejects_failed_terminal_outcome(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("PETTRIP_BASE_URL", "http://fixture")
    monkeypatch.setenv("PETTRIP_API_KEY", "fixture-key")
    responses = iter(
        [
            (201, {"session_id": "session-1"}),
            (202, {"output": {"structured_data": {"destination_id": "destination-1"}}}),
            (200, {"phase": "done", "done": True, "terminal_outcome": "partial_scene_failure", "scene_artifacts": []}),
        ]
    )
    monkeypatch.setattr(run_real_provider_demo, "request_json", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(sys, "argv", ["run_real_provider_demo"])
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("\n"))

    assert run_real_provider_demo.main() == 1
    assert "partial_scene_failure" in capsys.readouterr().err
