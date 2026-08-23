from __future__ import annotations

from agent_service.scripts import run_real_provider_demo


def test_real_demo_requires_explicit_service_configuration(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PETTRIP_BASE_URL", raising=False)
    monkeypatch.delenv("PETTRIP_API_KEY", raising=False)
    assert run_real_provider_demo.main() == 2
    assert "PETTRIP_BASE_URL" in capsys.readouterr().err
