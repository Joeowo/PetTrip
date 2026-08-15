"""统一输入 API：正例生成 input.json 与 job.accepted，反例 4xx 且不创建目录。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import DEFAULT_INPUT, Env, source_run_dir  # noqa: F401


def test_post_run_creates_input_and_accepted_event(env: Env) -> None:
    client = env.start()
    response = client.post("/runs", json={"run_id": "session4-alpha", "input": DEFAULT_INPUT})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run_id"] == "session4-alpha"
    assert body["snapshot"] == "scene-snapshot.json"

    run_dir = env.state_dir / "session4-alpha"
    assert (run_dir / "input.json").is_file()
    stored = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
    assert stored["run_id"] == "session4-alpha"
    assert stored["input"] == DEFAULT_INPUT
    assert stored["model_calls"] == "none (session4 replay-based run)"

    # SQLite 与 run 目录双落盘的 job.accepted
    events = client.get("/runs/session4-alpha").json()["events"]
    assert [event["event"] for event in events] == ["job.accepted"]

    # 重建产物就绪，快照通过 v0.2 契约且槽位未放置
    snapshot = client.get("/snapshot").json()
    assert snapshot["schema_version"] == "0.2"
    assert snapshot["build_slots"][0].get("placed_prefab") is None
    assert (run_dir / "scene-snapshot.json").is_file()
    assert (run_dir / "content-ready.json").is_file()


def test_post_run_missing_input_rejected_without_directory(env: Env) -> None:
    client = env.start()
    response = client.post("/runs", json={"run_id": "session4-missing-input"})
    assert response.status_code == 422
    assert not (env.state_dir / "session4-missing-input").exists()


def test_post_run_blank_input_rejected(env: Env) -> None:
    client = env.start()
    response = client.post("/runs", json={"run_id": "session4-blank", "input": ""})
    assert response.status_code == 422
    assert not (env.state_dir / "session4-blank").exists()


def test_post_run_invalid_run_id_rejected(env: Env) -> None:
    client = env.start()
    for bad_id in ["../escape", "UPPER", "space id", "_leading"]:
        response = client.post("/runs", json={"run_id": bad_id, "input": DEFAULT_INPUT})
        assert response.status_code == 422, bad_id
    assert list(env.state_dir.iterdir()) == []


def test_post_run_duplicate_rejected(env: Env) -> None:
    client = env.start()
    first = client.post("/runs", json={"run_id": "session4-dup", "input": DEFAULT_INPUT})
    assert first.status_code == 201
    second = client.post("/runs", json={"run_id": "session4-dup", "input": DEFAULT_INPUT})
    assert second.status_code == 409


def test_unknown_run_query_returns_404(env: Env) -> None:
    client = env.start()
    assert client.get("/runs/session4-nobody").status_code == 404
