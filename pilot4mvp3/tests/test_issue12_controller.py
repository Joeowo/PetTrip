import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw


SCRIPT = Path(__file__).parents[1] / "scripts" / "issue12_controller.py"
spec = importlib.util.spec_from_file_location("issue12_controller", SCRIPT)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def test_nearest_even_and_half_up_are_explicit():
    assert controller.nearest_even(107.52) == 108
    assert controller.nearest_even(106.9) == 106
    assert controller.half_up(10.5) == 11


def test_single_circle_detection_and_multiple_fail_closed(tmp_path):
    clean = tmp_path / "clean.png"
    one = tmp_path / "one.png"
    two = tmp_path / "two.png"
    Image.new("RGB", (100, 80), "white").save(clean)
    image = Image.new("RGB", (100, 80), "white")
    ImageDraw.Draw(image).ellipse((30, 20, 49, 39), fill="black")
    image.save(one)
    result = controller.detect_single_black_circle(clean, one)
    assert result["planned_locator_center"] == [40, 30]

    ImageDraw.Draw(image).ellipse((65, 45, 79, 59), fill="black")
    image.save(two)
    with pytest.raises(ValueError, match="found 2"):
        controller.detect_single_black_circle(clean, two)


def test_deterministic_aperture_uses_original_and_even_diameter(tmp_path):
    source = tmp_path / "source.png"
    output = tmp_path / "aperture.png"
    Image.new("RGB", (160, 90), (10, 20, 30)).save(source)

    record = controller.draw_deterministic_aperture(source, output, (80, 45))

    assert record["diameter"] == 12
    assert record["radius"] == 6
    with Image.open(output) as result:
        assert result.getpixel((0, 0)) == (10, 20, 30)
        assert result.getpixel((80, 45)) == (0, 0, 0)


def test_prompt_layers_have_fixed_order():
    destination = {
        "scene_requirement": "req",
        "scenes": [{"anchor": "A"}, {"anchor": "B"}],
    }
    layers = controller.build_prompt_layers(
        destination,
        {"description": "style"},
        {"prompt": "composition"},
        {"include_character_reference": False},
    )
    assert list(layers) == controller.LAYER_KEYS
    assert "不输入宠物参考图" in layers["current_anchor_action"]


def test_snapshot_approval_must_match(tmp_path):
    instance = controller.ExperimentController(root=tmp_path, assets_module=object())
    run_dir = tmp_path / "issue12" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    manifest = {
        "base_candidates": {
            "M0": {
                "status": "pending",
                "snapshot": {"snapshot_sha256": "abc"},
                "approval": None,
            }
        },
        "events": [],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="mismatch"):
        instance.approve_call("run-1", "M0", "wrong")


def test_approved_snapshot_rejects_mutation_and_path_escape(tmp_path):
    class Assets:
        @staticmethod
        def sha256_file(path):
            return "asset-hash"

    instance = controller.ExperimentController(root=tmp_path, assets_module=Assets())
    run_dir = tmp_path / "issue12" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    snapshot = {
        "prompt": {"rendered": "approved"},
        "references": [
            {"path": "assets/style.png", "sha256": "asset-hash"}
        ],
        "request": {"model": "gpt-image-2"},
        "idem_key": "idem",
    }
    snapshot["snapshot_sha256"] = controller._snapshot_digest(snapshot)
    manifest = {
        "base_candidates": {
            "M0": {
                "status": "pending",
                "snapshot": snapshot,
                "approval": {"snapshot_sha256": snapshot["snapshot_sha256"]},
            }
        },
        "events": [],
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    manifest["base_candidates"]["M0"]["snapshot"]["prompt"]["rendered"] = "mutated"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="no longer matches"):
        instance.assert_start_allowed("run-1", "M0")

    snapshot["prompt"]["rendered"] = "approved"
    snapshot["references"][0]["path"] = "../../secret.txt"
    snapshot["snapshot_sha256"] = controller._snapshot_digest(snapshot)
    manifest["base_candidates"]["M0"]["snapshot"] = snapshot
    manifest["base_candidates"]["M0"]["approval"]["snapshot_sha256"] = snapshot["snapshot_sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes run"):
        instance.assert_start_allowed("run-1", "M0")


def test_select_base_binds_both_scenes_and_processes_locator(tmp_path):
    class Assets:
        @staticmethod
        def sha256_file(path):
            import hashlib
            return hashlib.sha256(path.read_bytes()).hexdigest()

    instance = controller.ExperimentController(root=tmp_path, assets_module=Assets())
    run_dir = tmp_path / "issue12" / "runs" / "run-1"
    base_path = run_dir / "base.png"
    locator_path = run_dir / "locator.png"
    run_dir.mkdir(parents=True)
    Image.new("RGB", (100, 80), "white").save(base_path)
    locator = Image.new("RGB", (100, 80), "white")
    ImageDraw.Draw(locator).ellipse((30, 20, 49, 39), fill="black")
    locator.save(locator_path)
    base_hash = Assets.sha256_file(base_path)
    step_names = (
        "environment_base",
        "semantic_locator",
        "scan_planned_center",
        "draw_deterministic_aperture",
        "final_pet_scene",
    )
    scenes = {
        name: {
            "steps": {
                step: {"status": "pending", "attempts": [], "review": None}
                for step in step_names
            }
        }
        for name in ("A", "B")
    }
    manifest = {
        "base_candidates": {
            "M0": {
                "status": "continued",
                "attempts": [{"outputs": [{"path": "base.png", "sha256": base_hash}]}],
            }
        },
        "workflows": {"G06": {"shared_environment": {}, "scenes": scenes}},
        "events": [],
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    shared = instance.select_base_candidate("run-1", "M0", "selected")
    assert shared["sha256"] == base_hash
    selected = instance.load("run-1")
    assert selected["workflows"]["G06"]["scenes"]["A"]["steps"]["environment_base"]["status"] == "continued"
    assert selected["workflows"]["G06"]["scenes"]["B"]["steps"]["environment_base"]["outputs"][0]["sha256"] == base_hash

    selected["workflows"]["G06"]["scenes"]["A"]["steps"]["semantic_locator"]["status"] = "continued"
    instance.save("run-1", selected)
    result = instance.process_locator_output("run-1", "G06", "A", locator_path)
    assert result["measurement"]["planned_locator_center"] == [40, 30]
    processed = instance.load("run-1")
    steps = processed["workflows"]["G06"]["scenes"]["A"]["steps"]
    assert steps["scan_planned_center"]["status"] == "waiting_for_review"
    assert steps["draw_deterministic_aperture"]["status"] == "waiting_for_review"
