import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "issue12_full_runner.py"
spec = importlib.util.spec_from_file_location("issue12_full_runner", SCRIPT)
full = importlib.util.module_from_spec(spec)
spec.loader.exec_module(full)
PROJECT = Path(__file__).parents[1]
CONFIG = PROJECT / "issue12" / "experiment.json"
SOURCE_RUN = PROJECT / "issue12" / "runs" / "issue12-preflight-001"


def _runner(tmp_path):
    root = tmp_path / "pilot4mvp3"
    (root / "issue12" / "runs").mkdir(parents=True)
    target = root / "issue12" / "runs" / "issue12-preflight-001"
    target.mkdir(parents=True)
    source_manifest = json.loads((SOURCE_RUN / "manifest.json").read_text(encoding="utf-8"))
    for route in full.ROUTES:
        output = source_manifest["base_candidates"][route]["attempts"][-1]["outputs"][0]
        source = SOURCE_RUN / output["path"]
        destination = target / output["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    (target / "manifest.json").write_text(json.dumps(source_manifest), encoding="utf-8")
    runner = full.FullRunner(root=root)
    runner.assets_module = full.ASSETS if hasattr(runner, "assets_module") else None
    return runner


def test_full_plan_has_exact_matrix_and_reference_policies(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    monkeypatch.setattr(full.ASSETS, "prepare_assets", lambda config, run: _assets(run))

    manifest = runner.prepare(CONFIG, "full-run", "issue12-preflight-001")
    summary = runner.preflight("full-run")

    assert summary["counts"] == {"environment": 6, "locator": 16, "final": 16}
    assert len(manifest["calls"]) == 38
    assert len({call["spec"]["idem_key"] for call in manifest["calls"].values()}) == 38
    assert [item["role"] for item in manifest["calls"]["environment:G02:M0"]["spec"]["references"]] == ["style"]
    assert [item["role"] for item in manifest["calls"]["environment:G02:M1"]["spec"]["references"]] == ["style", "character_bottom"]
    assert [item["role"] for item in manifest["calls"]["locator:G06:M1:A"]["spec"]["references"]] == ["route_environment"]
    assert [item["role"] for item in manifest["calls"]["final:G06:M1:A"]["spec"]["references"]] == ["deterministic_aperture", "character_bottom"]
    assert manifest["artifacts"]["environment:G06:M0"]["origin"]["run_id"] == "issue12-preflight-001"


def test_plan_mutation_invalidates_approval(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    monkeypatch.setattr(full.ASSETS, "prepare_assets", lambda config, run: _assets(run))
    manifest = runner.prepare(CONFIG, "full-run", "issue12-preflight-001")
    runner.approve_plan("full-run", manifest["plan"]["plan_sha256"])
    changed = runner.load("full-run")
    changed["calls"]["environment:G02:M0"]["spec"]["prompt"]["rendered"] = "mutated"
    runner.save("full-run", changed)

    with pytest.raises(ValueError, match="plan digest|snapshot"):
        runner.preflight("full-run")
    with pytest.raises(ValueError, match="plan digest|snapshot"):
        runner.execute("full-run")


def test_cross_route_reference_mutation_is_rejected(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    monkeypatch.setattr(full.ASSETS, "prepare_assets", lambda config, run: _assets(run))
    runner.prepare(CONFIG, "full-run", "issue12-preflight-001")
    manifest = runner.load("full-run")
    call = manifest["calls"]["locator:G02:M0:A"]
    call["spec"]["references"][0]["artifact_id"] = "environment:G08:M1"
    call["spec"]["snapshot_sha256"] = full._snapshot(call)
    plan_body = {
        "ordered_call_ids": list(manifest["calls"]),
        "snapshot_sha256s": [item["spec"]["snapshot_sha256"] for item in manifest["calls"].values()],
        "imported_environment_sha256s": [manifest["artifacts"][f"environment:G06:{route}"]["sha256"] for route in full.ROUTES],
    }
    manifest["plan"]["ordered_call_ids"] = plan_body["ordered_call_ids"]
    manifest["plan"]["snapshot_sha256s"] = plan_body["snapshot_sha256s"]
    manifest["plan"]["plan_sha256"] = full._digest(plan_body)
    runner.save("full-run", manifest)

    with pytest.raises(ValueError, match="reference identity mismatch"):
        runner.preflight("full-run")


def test_dynamic_artifact_must_match_its_producer_result(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    monkeypatch.setattr(full.ASSETS, "prepare_assets", lambda config, run: _assets(run))
    runner.prepare(CONFIG, "full-run", "issue12-preflight-001")
    manifest = runner.load("full-run")
    call = manifest["calls"]["environment:G02:M0"]
    call["status"] = "succeeded"
    artifact = {
        "kind": "environment",
        "producer_call_id": call["id"],
        "destination": "G02",
        "route": "M0",
        "scene": None,
        "path": "artifacts/environments/G02/M0.png",
        "sha256": "correct",
    }
    call["result"] = {"outputs": [dict(artifact)]}
    manifest["artifacts"][call["id"]] = dict(artifact)
    runner._validate_artifact_producer(manifest, call["id"], artifact)

    manifest["artifacts"][call["id"]]["path"] = "artifacts/environments/G08/M1.png"
    with pytest.raises(ValueError, match="producer result mismatch"):
        runner._validate_artifact_producer(
            manifest, call["id"], manifest["artifacts"][call["id"]]
        )


def test_failed_environment_blocks_only_same_route(tmp_path, monkeypatch):
    runner = _runner(tmp_path)
    monkeypatch.setattr(full.ASSETS, "prepare_assets", lambda config, run: _assets(run))
    runner.prepare(CONFIG, "full-run", "issue12-preflight-001")
    manifest = runner.load("full-run")
    manifest["calls"]["environment:G02:M0"]["status"] = "failed"
    runner._propagate_blocked(manifest)

    assert manifest["calls"]["locator:G02:M0:A"]["status"] == "blocked"
    assert manifest["calls"]["final:G02:M0:A"]["status"] == "blocked"
    assert manifest["calls"]["locator:G02:M1:A"]["status"] == "pending"
    assert manifest["calls"]["locator:G03:M0:A"]["status"] == "pending"


def _assets(run_dir):
    styles = []
    for cell in ("E42", "E3", "E12", "E15"):
        path = run_dir / "assets" / "styles" / f"{cell}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cell.encode())
        styles.append({"cell": cell, "path": path.relative_to(run_dir).as_posix(), "output_sha256": full.ASSETS.sha256_file(path), "width": 1, "height": 1, "description": f"style {cell}"})
    bottom = run_dir / "assets" / "character" / "bottom.png"
    top = run_dir / "assets" / "character" / "top.png"
    bottom.parent.mkdir(parents=True, exist_ok=True)
    bottom.write_bytes(b"bottom"); top.write_bytes(b"top")
    return {
        "styles": styles,
        "compositions": [
            {"row": 9, "prompt": "S curve"},
            {"row": 10, "prompt": "layers"},
            {"row": 19, "prompt": "scroll"},
        ],
        "character": {
            "source": {"sha256": "source"},
            "top": {"path": top.relative_to(run_dir).as_posix(), "sha256": full.ASSETS.sha256_file(top)},
            "bottom": {"path": bottom.relative_to(run_dir).as_posix(), "sha256": full.ASSETS.sha256_file(bottom), "width": 1, "height": 1},
        },
    }
