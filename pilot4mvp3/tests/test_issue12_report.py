import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "issue12_report.py"
spec = importlib.util.spec_from_file_location("issue12_report", SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_report_is_static_escaped_and_does_not_modify_manifest(tmp_path):
    manifest = {
        "run_id": "run-1",
        "assets": {
            "styles": [{"cell": "E12", "path": "assets/style.png", "output_sha256": "abc", "width": 20, "height": 10}],
            "character": {
                "top": {"path": "assets/top.png", "sha256": "top", "width": 20, "height": 10},
                "bottom": {"path": "assets/bottom.png", "sha256": "bottom", "width": 20, "height": 11},
            },
        },
        "base_candidates": {
            "M0": {
                "status": "pending",
                "snapshot": {
                    "snapshot_sha256": "snap",
                    "prompt": {"layers": {"fixed": "<script>alert(1)</script>"}, "rendered": "safe"},
                    "references": [{"order": 1, "role": "style", "path": "assets/style.png", "sha256": "abc"}],
                    "request": {"model": "gpt-image-2"},
                    "idem_key": "idem",
                },
                "attempts": [],
            }
        },
        "events": [],
    }
    manifest_path = tmp_path / "manifest.json"
    original = json.dumps(manifest, ensure_ascii=False)
    manifest_path.write_text(original, encoding="utf-8")
    output = tmp_path / "evidence.html"

    report.render_evidence(manifest_path, output)

    page = output.read_text(encoding="utf-8")
    assert "&lt;script&gt;" in page
    assert '<img src="assets/style.png"' in page
    assert "planned locator center" in page
    assert manifest_path.read_text(encoding="utf-8") == original


def test_report_redacts_forbidden_credential_material(tmp_path):
    manifest = {
        "run_id": "run",
        "assets": {},
        "base_candidates": {
            "M0": {
                "status": "pending",
                "snapshot": {"request": {"api_key": "secret"}},
                "attempts": [],
            }
        },
        "events": [],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    output = tmp_path / "out.html"
    report.render_evidence(manifest_path, output)
    page = output.read_text(encoding="utf-8").lower()
    assert "api_key" not in page
    assert "secret" not in page
    assert "[redacted]" in page
