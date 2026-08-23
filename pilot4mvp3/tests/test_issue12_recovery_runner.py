import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw


SCRIPT = Path(__file__).parents[1] / "scripts" / "issue12_recovery_runner.py"
spec = importlib.util.spec_from_file_location("issue12_recovery_runner", SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def _source_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    config = {"api": {"model": "mock", "size": "16:9", "resolution": "2k", "quality": "high", "timeout": 1}, "aperture": {"short_edge_ratio": 0.2}, "locator_detection": {"minimum_area": 25, "minimum_bbox_side": 10}}
    artifacts = {}
    calls = {}
    character = source / "assets" / "character" / "bottom.png"
    character.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), "black").save(character)
    artifacts["character:bottom"] = recovery._artifact(
        character, source, "character_bottom"
    )
    for route in recovery.ROUTES:
        env = source / "artifacts" / "environments" / "G06" / f"{route}.png"
        env.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 160), (180, 180, 180)).save(env)
        artifacts[f"environment:G06:{route}"] = recovery._artifact(env, source, "environment")
    for destination in recovery.DESTINATIONS:
        for route in recovery.ROUTES:
            if destination != "G06":
                source_env = source / "artifacts" / "environments" / "G06" / f"{route}.png"
                env = source / "artifacts" / "environments" / destination / f"{route}.png"
                env.parent.mkdir(parents=True, exist_ok=True)
                env.write_bytes(source_env.read_bytes())
                artifacts[f"environment:{destination}:{route}"] = recovery._artifact(env, source, "environment")
            env = source / "artifacts" / "environments" / "G06" / f"{route}.png"
            for scene in recovery.SCENES:
                call_id = f"locator:{destination}:{route}:{scene}"
                locator = source / "artifacts" / "locators" / destination / route / f"{scene}.png"
                locator.parent.mkdir(parents=True, exist_ok=True)
                image = Image.open(env).copy()
                ImageDraw.Draw(image).ellipse((80, 60, 139, 119), fill="black")
                image.save(locator)
                artifact = recovery._artifact(locator, source, "locator")
                artifacts[call_id] = artifact
                calls[call_id] = {"id": call_id, "phase": "locator", "destination": destination, "route": route, "scene": scene, "status": "succeeded", "result": {}}
                aperture_id = f"aperture:{destination}:{route}:{scene}"
                aperture = source / "artifacts" / "apertures" / destination / route / f"{scene}.png"
                recovery.BASE.draw_deterministic_aperture(env, aperture, (110, 90), 0.2)
                artifacts[aperture_id] = recovery._artifact(aperture, source, "deterministic_aperture") | {"center": [110, 90]}
                if len([key for key in artifacts if key.startswith("aperture:")]) < 7:
                    final = source / "artifacts" / "finals" / destination / route / f"{scene}.png"
                    final.parent.mkdir(parents=True, exist_ok=True)
                    final.write_bytes(b"final")
                    artifacts[f"final:{destination}:{route}:{scene}"] = recovery._artifact(final, source, "final")
    for call_id in recovery.FAILED_CALLS:
        _, destination, route, scene = call_id.split(":")
        calls[call_id] = {"id": call_id, "phase": "locator", "destination": destination, "route": route, "scene": scene, "status": "failed", "result": {}}
        final_id = f"final:{destination}:{route}:{scene}"
        calls[final_id] = {"id": final_id, "phase": "final", "destination": destination, "route": route, "scene": scene, "status": "blocked", "result": {}, "spec": {"references": [{"role": "deterministic_aperture"}, {"role": "character_bottom"}], "request": config["api"], "output_path": f"artifacts/finals/{destination}/{route}/{scene}.png", "idem_key": final_id}}
    manifest = {"run_id": "issue12-full-001", "config": config, "artifacts": artifacts, "calls": calls}
    path = source / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    registered = tmp_path / "issue12" / "runs" / "issue12-full-001"
    registered.parent.mkdir(parents=True)
    shutil.copytree(source, registered)
    return registered


def test_prepare_recovery_is_immutable_and_has_ten_new_calls(tmp_path):
    _source_fixture(tmp_path)
    runner = recovery.RecoveryRunner(tmp_path)
    manifest = runner.prepare("issue12-full-001", "issue12-recovery-001")
    assert len(manifest["calls"]) == 10
    assert manifest["source"]["run_id"] == "issue12-full-001"
    assert manifest["counts"] == {"environment": 0, "locator": 10, "final": 10}
    assert {item["attempt"] for item in manifest["calls"].values()} == {None}
    assert all(item["spec"]["idem_key"].startswith("issue12-issue12-recovery-001-") for item in manifest["calls"].values())


def test_preflight_rejects_locator_center_mismatch(tmp_path):
    _source_fixture(tmp_path)
    runner = recovery.RecoveryRunner(tmp_path)
    runner.prepare("issue12-full-001", "run")
    manifest = runner.load("run")
    first = next(key for key in manifest["artifacts"] if key.startswith("aperture:"))
    manifest["artifacts"][first]["center"] = [1, 2]
    runner.save("run", manifest)
    with pytest.raises(ValueError, match="center"):
        runner.preflight("run")


def test_submission_unknown_never_gets_second_attempt(tmp_path, monkeypatch):
    _source_fixture(tmp_path)
    runner = recovery.RecoveryRunner(tmp_path)
    runner.prepare("issue12-full-001", "run")
    manifest = runner.load("run")
    call_id = next(iter(manifest["calls"]))
    submissions = []

    class Relay:
        class requests:
            class Session:
                pass

        @staticmethod
        def normalize_ref(path):
            return path

        @staticmethod
        def submit_task(session, base, payload, idem_key, on_created=None):
            submissions.append(idem_key)
            return {
                "ok": False,
                "error": {"code": "submission_unknown", "retryable": False},
            }

    monkeypatch.setattr(runner, "_session", lambda: (Relay(), object(), "https://task-api-1-cn.65535.space"))
    runner.approve_plan("run", manifest["plan"]["plan_sha256"])
    runner.execute("run")
    runner.execute("run")
    result = runner.load("run")["calls"][call_id]
    assert result["status"] == "submission_unknown"
    assert result["attempt"]["attempt_number"] == 1
    assert submissions == [
        item["spec"]["idem_key"] for item in manifest["calls"].values()
    ]
    assert len(submissions) == 10
