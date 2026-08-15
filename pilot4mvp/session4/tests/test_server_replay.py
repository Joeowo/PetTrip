"""离线重放：服务重启后仅从既有 artifact 重建，不调用模型，写入 job.replayed。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.conftest import DEFAULT_INPUT, Env, accepted_client, make_png_base64, v2_payload_with_shelter  # noqa: F401


def _drive_full_flow(client: TestClient) -> None:
    """统一输入 -> v2 放置 -> 报告，把 run 推进到 v2 状态。"""
    v2 = client.post("/runs/session4-test-run/snapshot-v2", json=v2_payload_with_shelter(client))
    assert v2.status_code == 201, v2.text
    meta = client.get("/snapshot-meta").json()
    report = client.post(
        "/runs/session4-test-run/reports",
        json={
            "run_id": "session4-test-run",
            "snapshot_sha256": meta["sha256"],
            "checks": [{"name": "shelter_placed", "passed": True, "detail": "ok"}],
            "screenshot_png_base64": make_png_base64(),
        },
    )
    assert report.status_code == 201, report.text


def test_replay_after_service_restart_restores_v2(env: Env) -> None:
    first = env.start()
    accepted = first.post("/runs", json={"run_id": "session4-test-run", "input": DEFAULT_INPUT})
    assert accepted.status_code == 201
    _drive_full_flow(first)
    before = first.get("/snapshot").json()
    first_sha = first.get("/snapshot-meta").json()["sha256"]

    # 服务重启：全新 app 实例，同一 state 目录与 SQLite，仅恢复活动 run
    restarted = env.start(run_id="session4-test-run")
    replay = restarted.post("/runs/session4-test-run/replay")
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["snapshot"] == "scene-snapshot-v2.json"
    assert body["business_fields_match"] is True

    after = restarted.get("/snapshot").json()
    assert after == before
    assert restarted.get("/snapshot-meta").json()["sha256"] == first_sha

    events = restarted.get("/runs/session4-test-run").json()["events"]
    assert [event["event"] for event in events] == ["job.accepted", "job.replayed"]
    assert events[-1]["detail"]["model_calls"] == "none"


def test_replay_runs_without_active_run_reference(env: Env) -> None:
    """重放不依赖内存活动状态：重启后不设 active run 也能按 run_id 重放。"""
    first = env.start()
    assert first.post("/runs", json={"run_id": "session4-test-run", "input": DEFAULT_INPUT}).status_code == 201
    replay_only = env.start(run_id="session4-test-run")
    assert replay_only.post("/runs/session4-test-run/replay").status_code == 200


def test_replay_detects_tampered_artifact(env: Env) -> None:
    """fail-closed：world-spec 被篡改后重建结果与落盘快照不一致，重放必须拒绝。"""
    client = env.start()
    assert client.post("/runs", json={"run_id": "session4-test-run", "input": DEFAULT_INPUT}).status_code == 201
    _drive_full_flow(client)

    run_dir = env.state_dir / "session4-test-run"
    spec = json.loads((run_dir / "world-spec.json").read_text(encoding="utf-8"))
    spec["landmark"] = "castle"
    (run_dir / "world-spec.json").write_text(json.dumps(spec), encoding="utf-8")

    restarted = env.start(run_id="session4-test-run")
    replay = restarted.post("/runs/session4-test-run/replay")
    assert replay.status_code == 409
    # 落盘快照未被破坏性覆盖
    stored = json.loads((run_dir / "scene-snapshot-v2.json").read_text(encoding="utf-8"))
    assert stored["build_slots"][0]["placed_prefab"] == "small_shelter"


def test_replay_unknown_run_returns_404(env: Env) -> None:
    client = env.start()
    assert client.post("/runs/session4-nobody/replay").status_code == 404
