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


def test_report_renders_issue12_run_02_evidence(tmp_path):
    calls = [
        {
            "id": f"call-{index:02d}",
            "destination": "G06" if index == 0 else "G02",
            "route": "R1",
            "scene": "A",
            "kind": "environment" if index == 0 else "final",
            "status": "completed" if index < 36 else "blocked",
            "outputs": [{"path": f"evidence/{index}.png", "sha256": f"sha-{index}"}],
            "measurement": {"planned_locator_center": [40, 30]} if index == 0 else None,
            "task_safe_metadata": {
                "task_id": f"task-{index}",
                "provider_status": "succeeded",
                "Authorization": "Bearer secret",
                "signedURL": "https://secret.example/image",
            },
            "error": "<failed>" if index == 36 else None,
            "blocked": {"reason": "review required"} if index >= 36 else None,
        }
        for index in range(38)
    ]
    manifest = {
        "schema_version": "issue12-run/0.2",
        "run_id": "run-02",
        "plan": {
            "sha256": "plan-sha",
            "approval": {"decision": "approved", "approved_at": "now"},
        },
        "calls": calls,
        "destinations": {
            "G06": {
                "provenance": {
                    "kind": "imported",
                    "source_run": "run-01",
                    "source_sha256": "source-sha",
                }
            }
        },
        "final_review": {"decision": "continue", "note": "<looks good>"},
    }
    manifest_path = tmp_path / "manifest.json"
    original = json.dumps(manifest)
    manifest_path.write_text(original, encoding="utf-8")

    output = tmp_path / "evidence.html"
    report.render_evidence(manifest_path, output)

    page = output.read_text(encoding="utf-8")
    lowered = page.lower()
    assert "plan-sha" in page and "approved" in page
    assert "38 calls" in page and "completed: 36" in page and "blocked: 2" in page
    assert "G06" in page and "R1" in page and "scene A" in page
    assert '<img src="evidence/0.png"' in page
    assert "planned_locator_center" in page and "task-0" in page
    assert "imported" in page and "run-01" in page and "source-sha" in page
    assert "Final review" in page and "&lt;looks good&gt;" in page
    for forbidden in ("authorization", "bearer secret", "signedurl", "secret.example", "data:", "authcode="):
        assert forbidden not in lowered
    assert manifest_path.read_text(encoding="utf-8") == original


def test_report_02_rejects_paths_outside_run(tmp_path):
    manifest = {
        "schema_version": "issue12-run/0.2",
        "run_id": "run-02",
        "calls": [{
            "destination": "G02",
            "route": "R1",
            "scene": "A",
            "kind": "locator",
            "status": "completed",
            "outputs": [{"path": "../outside.png"}, {"path": "data:image/png;base64,abc"}],
        }],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "evidence.html"

    report.render_evidence(manifest_path, output)

    page = output.read_text(encoding="utf-8").lower()
    assert "../outside.png" not in page
    assert "data:image" not in page
    assert "<img" not in page
