"""Recover the ten failed Issue 12 final image calls safely."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
FULL = importlib.util.spec_from_file_location(
    "issue12_full_runner_recovery", ROOT / "scripts" / "issue12_full_runner.py"
)
FULL_MODULE = importlib.util.module_from_spec(FULL)
assert FULL.loader is not None
FULL.loader.exec_module(FULL_MODULE)
BASE = FULL_MODULE.BASE
FullRunner = FULL_MODULE.FullRunner
ASSETS = FULL_MODULE.ASSETS
ROUTES = FULL_MODULE.ROUTES
SCENES = FULL_MODULE.SCENES
DESTINATIONS = FULL_MODULE.DESTINATIONS
FINAL_PROMPT = FULL_MODULE._final_prompt
SOURCE_RUN_ID = "issue12-full-001"
FAILED_CALLS = (
    "locator:G02:M0:A", "locator:G02:M0:B", "locator:G03:M0:A",
    "locator:G03:M0:B", "locator:G03:M1:A", "locator:G03:M1:B",
    "locator:G06:M0:A", "locator:G06:M0:B", "locator:G06:M1:A",
    "locator:G06:M1:B",
)


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _artifact(path: Path, root: Path, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "sha256": ASSETS.sha256_file(path),
        "size": list(Image.open(path).size) if kind != "final" else None,
    }


class RecoveryRunner(FullRunner):
    """A recovery run whose source run is read-only and never resubmitted."""

    def prepare(self, source_run: str, run_id: str) -> dict[str, Any]:
        if not source_run or any(token in source_run for token in ("/", "\\", "..")):
            raise ValueError("invalid source run id")
        source_dir, source_manifest_path = self.paths(source_run)
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        if source_manifest.get("run_id") != SOURCE_RUN_ID:
            raise ValueError("source run must be issue12-full-001")
        run_dir, manifest_path = self.paths(run_id)
        if manifest_path.exists():
            raise FileExistsError(run_id)
        run_dir.mkdir(parents=True)
        manifest_hash = ASSETS.sha256_file(source_manifest_path)
        current_config = json.loads(
            (ROOT / "issue12" / "experiment.json").read_text(encoding="utf-8")
        )
        recovery_config = json.loads(json.dumps(source_manifest["config"]))
        for key in (
            "api",
            "aperture",
            "destinations",
            "routes",
            "reference_policies",
            "execution_policy",
            "locator_detection",
        ):
            recovery_config[key] = current_config[key]
        artifacts: dict[str, Any] = {}
        for artifact_id, source_artifact in source_manifest["artifacts"].items():
            source_path = (source_dir / source_artifact["path"]).resolve()
            if not source_path.is_file() or ASSETS.sha256_file(source_path) != source_artifact["sha256"]:
                raise ValueError(f"source artifact hash mismatch: {artifact_id}")
            destination = run_dir / source_artifact["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            if ASSETS.sha256_file(destination) != source_artifact["sha256"]:
                raise ValueError(f"copied artifact hash mismatch: {artifact_id}")
            artifacts[artifact_id] = dict(source_artifact)

        calls: dict[str, Any] = {}
        failed_locators = []
        failed_finals = []
        for call_id, source_call in source_manifest["calls"].items():
            if source_call["phase"] == "locator":
                locator = artifacts[call_id]
                destination = source_call["destination"]
                route = source_call["route"]
                scene = source_call["scene"]
                environment = artifacts[f"environment:{destination}:{route}"]
                measurement = BASE.detect_black_locator(
                    run_dir / environment["path"], run_dir / locator["path"],
                    recovery_config["locator_detection"],
                )
                locator["recovery_detection"] = measurement
                aperture_id = f"aperture:{destination}:{route}:{scene}"
                old_aperture = source_manifest["artifacts"].get(aperture_id)
                if old_aperture and source_call["status"] == "succeeded":
                    old_center = old_aperture.get("center") or []
                    new_center = measurement["planned_locator_center"]
                    if (
                        len(old_center) != 2
                        or max(abs(old_center[index] - new_center[index]) for index in (0, 1)) > 1
                    ):
                        raise ValueError(f"aperture center mismatch: {aperture_id}")
                    old_aperture["recovery_center_delta"] = [
                        new_center[index] - old_center[index] for index in (0, 1)
                    ]
                else:
                    output = run_dir / "artifacts" / "apertures" / destination / route / f"{scene}.png"
                    aperture = BASE.draw_deterministic_aperture(
                        run_dir / environment["path"], output,
                        tuple(measurement["planned_locator_center"]),
                        source_manifest["config"]["aperture"]["short_edge_ratio"],
                    )
                    aperture.update({
                        "kind": "deterministic_aperture",
                        "measurement": measurement,
                        "derived_from_call_id": call_id,
                        "destination": destination,
                        "route": route,
                        "scene": scene,
                        "path": output.relative_to(run_dir).as_posix(),
                    })
                    artifacts[aperture_id] = aperture
                if source_call["status"] != "succeeded":
                    failed_locators.append(call_id)
            elif source_call["phase"] == "final" and source_call["status"] in {"failed", "blocked"}:
                failed_finals.append(call_id)
        if len(failed_locators) != 10 or len(failed_finals) != 10:
            raise ValueError("source failure matrix must contain ten locator and ten final failures")

        destinations = {
            item["id"]: item for item in recovery_config["destinations"]
        }
        for ordinal, call_id in enumerate(failed_finals, 1):
            source_call = source_manifest["calls"][call_id]
            destination_config = destinations[source_call["destination"]]
            scene_config = next(
                item
                for item in destination_config["scenes"]
                if item["id"] == source_call["scene"]
            )
            spec = json.loads(json.dumps(source_call["spec"]))
            spec["prompt"] = FINAL_PROMPT(destination_config, scene_config)
            spec["idem_key"] = f"issue12-{run_id}-{call_id.replace(':', '-').lower()}"
            spec["output_path"] = f"artifacts/recovery-finals/{source_call['destination']}/{source_call['route']}/{source_call['scene']}.png"
            spec["request"] = {
                "provider": "65535",
                **recovery_config["api"],
                "base_url": "https://task-api-1-cn.65535.space",
            }
            spec["references"] = [
                {
                    "order": 1,
                    "role": "deterministic_aperture",
                    "artifact_id": (
                        f"aperture:{source_call['destination']}:"
                        f"{source_call['route']}:{source_call['scene']}"
                    ),
                },
                {
                    "order": 2,
                    "role": "character_bottom",
                    "artifact_id": "character:bottom",
                },
            ]
            spec["depends_on"] = []
            spec.pop("snapshot_sha256", None)
            spec["snapshot_sha256"] = _digest(spec)
            calls[call_id] = {
                "id": call_id, "artifact_id": f"recovery_final:{call_id}", "ordinal": ordinal, "phase": "final",
                "destination": source_call["destination"], "route": source_call["route"],
                "scene": source_call["scene"], "status": "pending", "blocked_by": [],
                "spec": spec, "attempt": None, "result": None,
            }
        preserved_final_sha256s = [
            source_manifest["artifacts"][identifier]["sha256"]
            for identifier in sorted(source_manifest["artifacts"])
            if identifier.startswith("final:")
        ]
        plan_body = {
            "ordered_call_ids": list(calls),
            "snapshot_sha256s": [call["spec"]["snapshot_sha256"] for call in calls.values()],
            "source_manifest_sha256": manifest_hash,
            "detection_sha256s": [
                _digest(artifacts[identifier]["recovery_detection"])
                for identifier in sorted(artifacts)
                if identifier.startswith("locator:")
            ],
            "aperture_sha256s": [
                artifacts[identifier]["sha256"]
                for identifier in sorted(artifacts)
                if identifier.startswith("aperture:")
            ],
            "preserved_final_sha256s": preserved_final_sha256s,
        }
        manifest = {
            "schema_version": "issue12-recovery/0.1", "run_id": run_id,
            "source": {"run_id": SOURCE_RUN_ID, "manifest_sha256": manifest_hash},
            "config": recovery_config, "artifacts": artifacts, "calls": calls,
            "counts": {"environment": 0, "locator": len(failed_locators), "final": len(calls)},
            "plan": {"expected_remote_call_count": 10, **plan_body, "plan_sha256": _digest(plan_body), "approval": None},
            "final_review": {"status": "pending", "decision": None, "note": None}, "events": [],
        }
        self.save(run_id, manifest)
        return manifest

    def _validate_plan(self, manifest):
        if manifest["source"]["run_id"] != SOURCE_RUN_ID:
            raise ValueError("source run is not immutable")
        if len(manifest["calls"]) != 10 or any(call["phase"] != "final" for call in manifest["calls"].values()):
            raise ValueError("recovery must contain exactly ten final calls")
        expected_ids = {
            call_id.replace("locator:", "final:", 1)
            for call_id in FAILED_CALLS
        }
        if set(manifest["calls"]) != expected_ids:
            raise ValueError("recovery call identity mismatch")
        for call_id, call in manifest["calls"].items():
            _, destination, route, scene = call_id.split(":")
            if (call["destination"], call["route"], call["scene"]) != (destination, route, scene):
                raise ValueError(f"recovery call metadata mismatch: {call_id}")
            expected_aperture = f"aperture:{destination}:{route}:{scene}"
            refs = call["spec"]["references"]
            if [item["artifact_id"] for item in refs] != [expected_aperture, "character:bottom"]:
                raise ValueError(f"recovery reference identity mismatch: {call_id}")
        plan = manifest["plan"]
        body = {
            "ordered_call_ids": list(manifest["calls"]),
            "snapshot_sha256s": [
                call["spec"]["snapshot_sha256"]
                for call in manifest["calls"].values()
            ],
            "source_manifest_sha256": manifest["source"]["manifest_sha256"],
            "detection_sha256s": [
                _digest(manifest["artifacts"][identifier]["recovery_detection"])
                for identifier in sorted(manifest["artifacts"])
                if identifier.startswith("locator:")
            ],
            "aperture_sha256s": [
                manifest["artifacts"][identifier]["sha256"]
                for identifier in sorted(manifest["artifacts"])
                if identifier.startswith("aperture:")
            ],
            "preserved_final_sha256s": manifest["plan"].get("preserved_final_sha256s", []),
        }
        if _digest(body) != plan["plan_sha256"] or plan["ordered_call_ids"] != body["ordered_call_ids"]:
            raise ValueError("plan digest mismatch")
        for call in manifest["calls"].values():
            if _digest({key: value for key, value in call["spec"].items() if key != "snapshot_sha256"}) != call["spec"]["snapshot_sha256"]:
                raise ValueError(f"snapshot mismatch: {call['id']}")
            self._validate_reference_policy(call)

    def _validate_reference_policy(self, call):
        roles = [item["role"] for item in call["spec"]["references"]]
        if roles != ["deterministic_aperture", "character_bottom"]:
            raise ValueError(f"reference policy mismatch: {call['id']}")

    def _validate_artifact_producer(self, manifest, artifact_id, artifact):
        if artifact_id.startswith("aperture:") and artifact.get("derived_from_call_id"):
            return
        super()._validate_artifact_producer(manifest, artifact_id, artifact)

    def preflight(self, run_id: str) -> dict[str, Any]:
        manifest = self.load(run_id)
        self._validate_plan(manifest)
        source_dir, source_manifest_path = self.paths(manifest["source"]["run_id"])
        if ASSETS.sha256_file(source_manifest_path) != manifest["source"]["manifest_sha256"]:
            raise ValueError("source manifest changed")
        detection_count = sum(
            "recovery_detection" in artifact
            for artifact_id, artifact in manifest["artifacts"].items()
            if artifact_id.startswith("locator:")
        )
        aperture_count = sum(
            artifact_id.startswith("aperture:")
            for artifact_id in manifest["artifacts"]
        )
        preserved_final_count = sum(
            artifact_id.startswith("final:")
            for artifact_id in manifest["artifacts"]
        )
        if detection_count != 16 or aperture_count != 16 or preserved_final_count != 6:
            raise ValueError("recovery artifact matrix mismatch")
        if len(manifest["calls"]) != 10 or any(call["phase"] != "final" for call in manifest["calls"].values()):
            raise ValueError("recovery call matrix mismatch")
        for artifact_id, artifact in manifest["artifacts"].items():
            path = self.paths(run_id)[0] / artifact["path"]
            if not path.is_file():
                raise ValueError(f"artifact missing: {artifact_id}")
            if ASSETS.sha256_file(path) != artifact["sha256"]:
                raise ValueError(f"artifact hash mismatch: {artifact_id}")
            if artifact_id.startswith("aperture:"):
                locator_id = artifact_id.replace("aperture:", "locator:", 1)
                detected = manifest["artifacts"].get(locator_id, {}).get("recovery_detection", {})
                old_center = artifact.get("center") or []
                new_center = detected.get("planned_locator_center") or []
                if (
                    len(old_center) != 2
                    or len(new_center) != 2
                    or max(abs(old_center[index] - new_center[index]) for index in (0, 1)) > 1
                ):
                    raise ValueError(f"aperture center mismatch: {artifact_id}")
        return {
            "run_id": run_id,
            "counts": {
                "detection_selected": detection_count,
                "apertures": aperture_count,
                "preserved_final": preserved_final_count,
                "new_final_pending": len(manifest["calls"]),
            },
            "plan_sha256": manifest["plan"]["plan_sha256"],
        }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-recovery")
    prepare.add_argument("--source-run", required=True)
    prepare.add_argument("--run-id", required=True)
    for name in ("preflight", "execute", "status", "report"):
        item = commands.add_parser(name)
        item.add_argument("--run", required=True)
    approve = commands.add_parser("approve")
    approve.add_argument("--run", required=True)
    approve.add_argument("--plan-sha256", required=True)
    review = commands.add_parser("review-run")
    review.add_argument("--run", required=True)
    review.add_argument("--decision", choices=("accept", "reject"), required=True)
    review.add_argument("--note", required=True)
    args = parser.parse_args()
    runner = RecoveryRunner()
    if args.command == "prepare-recovery":
        result = runner.prepare(args.source_run, args.run_id)
        print(json.dumps({"run_id": result["run_id"], "plan_sha256": result["plan"]["plan_sha256"]}))
    elif args.command == "preflight":
        print(json.dumps(runner.preflight(args.run), indent=2))
    elif args.command == "approve":
        runner.approve_plan(args.run, args.plan_sha256)
    elif args.command == "execute":
        print(json.dumps(runner.execute(args.run), indent=2))
    elif args.command == "status":
        print(json.dumps(runner.status(args.run), indent=2))
    elif args.command == "review-run":
        runner.review_run(args.run, args.decision, args.note)
        print("review recorded")
    else:
        report = FULL_MODULE._module(
            "issue12_report_recovery", ROOT / "scripts" / "issue12_report.py"
        )
        run_dir, manifest_path = runner.paths(args.run)
        output = run_dir / "evidence.html"
        report.render_evidence(manifest_path, output)
        print(output)


if __name__ == "__main__":
    main()
