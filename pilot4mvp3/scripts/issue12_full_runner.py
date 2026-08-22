"""Full destination × route runner for the Issue 12 experiment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "issue12" / "runs"
TERMINAL = {"succeeded", "failed", "blocked"}
ROUTES = ("M0", "M1")
SCENES = ("A", "B")
DESTINATIONS = ("G02", "G03", "G06", "G08")


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ASSETS = _module("issue12_assets_full", ROOT / "scripts" / "issue12_assets.py")
BASE = _module("issue12_controller_base", ROOT / "scripts" / "issue12_controller.py")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _safe_relative(run_dir: Path, relative: str) -> Path:
    item = Path(relative)
    if item.is_absolute() or ".." in item.parts:
        raise ValueError(f"artifact path escapes run: {relative}")
    resolved = (run_dir / item).resolve()
    if run_dir.resolve() not in resolved.parents:
        raise ValueError(f"artifact path escapes run: {relative}")
    return resolved


def _prompt(layers: dict[str, str]) -> dict[str, Any]:
    rendered = "\n\n".join(f"[{index}] {text}" for index, text in enumerate(layers.values(), 1))
    return {"layers": layers, "rendered": rendered, "sha256": hashlib.sha256(rendered.encode()).hexdigest()}


def _environment_prompt(destination, style, composition, route):
    role = (
        "参考图2仅用于理解固定宠物形象的小尺寸比例和活动空间；输出不得出现宠物、局部、剪影、雕塑、玩偶、白底分栏或文字。"
        if route == "M1"
        else "不输入宠物参考图；按两个锚点预留小尺寸宠物活动空间。"
    )
    return _prompt({
        "fixed_boundaries": "生成16:9横版纯环境母图，手绘2D、自然克制、非摄影；固定镜头和单一主光；无人物、宠物、黑圈、UI、文字、Logo或水印。",
        "destination_requirement": destination["scene_requirement"],
        "style_description": style["description"],
        "composition_prompt": composition["prompt"],
        "current_anchor_action": f"预留两个空间分离、可站立、避开边缘和主地标的锚点：{destination['scenes'][0]['anchor']}；{destination['scenes'][1]['anchor']}。{role}",
    })


def _locator_prompt(destination, scene):
    return _prompt({
        "fixed_boundaries": "参考图1是不可变环境母图。只允许新增一个纯黑实心圆；禁止宠物、人物、文字、UI和任何第二标记。",
        "destination_requirement": destination["scene_requirement"],
        "style_description": "完全保持参考图的画风、颜色、材质、光照和镜头，不重新绘制环境。",
        "composition_prompt": "保持全部构图和地标不变，黑圈外像素不得改变。",
        "current_anchor_action": f"只在“{scene['anchor']}”的安全可站立承载面绘制一个黑色实心圆；避开天空、水面、悬崖和主体。该圆只是语义定位图，最终黑洞由程序重绘。",
    })


def _final_prompt(destination, scene):
    return _prompt({
        "fixed_boundaries": "参考图1是带确定性黑洞的环境，参考图2是固定宠物室外三视图。只在黑洞区域生成一只宠物并完全移除黑色；无第二角色、文字、UI、Logo或水印。",
        "destination_requirement": destination["scene_requirement"],
        "style_description": "宠物绘制必须融入参考图1的既有手绘2D画风、光照和色彩，同时保持参考图2的身份、配色和小尺寸比例。",
        "composition_prompt": "保持黑洞外环境、地标、镜头、构图和光照不变；宠物主体完整落在原黑洞区域，脚底接触承载面。",
        "current_anchor_action": f"位置锚点：{scene['anchor']}。唯一动作：{scene['action']}。",
    })


def _snapshot(call: dict[str, Any]) -> str:
    body = {key: value for key, value in call["spec"].items() if key != "snapshot_sha256"}
    return _digest(body)


class FullRunner:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.runs = root / "issue12" / "runs"

    def paths(self, run_id: str) -> tuple[Path, Path]:
        if not run_id or any(token in run_id for token in ("/", "\\", "..")):
            raise ValueError("invalid run_id")
        directory = self.runs / run_id
        return directory, directory / "manifest.json"

    def load(self, run_id: str) -> dict[str, Any]:
        return json.loads(self.paths(run_id)[1].read_text(encoding="utf-8"))

    def save(self, run_id: str, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = _now()
        _atomic(self.paths(run_id)[1], manifest)

    def prepare(self, config_path: Path, run_id: str, source_run: str) -> dict[str, Any]:
        run_dir, manifest_path = self.paths(run_id)
        if manifest_path.exists():
            raise FileExistsError(run_id)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self._validate_config(config)
        assets = ASSETS.prepare_assets(config_path, run_dir)
        artifacts: dict[str, Any] = {}
        for style in assets["styles"]:
            artifacts[f"style:{style['cell']}"] = {
                "kind": "style", "path": style["path"], "sha256": style["output_sha256"],
                "size": [style["width"], style["height"]],
            }
        bottom = assets["character"]["bottom"]
        artifacts["character:bottom"] = {
            "kind": "character_bottom", "path": bottom["path"], "sha256": bottom["sha256"],
            "size": [bottom["width"], bottom["height"]],
        }
        self._import_g06(run_dir, config, source_run, artifacts)
        calls = self._build_calls(run_id, config, assets)
        ordered = list(calls)
        plan_body = {
            "ordered_call_ids": ordered,
            "snapshot_sha256s": [calls[item]["spec"]["snapshot_sha256"] for item in ordered],
            "imported_environment_sha256s": [artifacts[f"environment:G06:{route}"]["sha256"] for route in ROUTES],
        }
        manifest = {
            "schema_version": "issue12-run/0.2", "run_id": run_id,
            "created_at": _now(), "updated_at": _now(), "config": config, "assets": assets,
            "plan": {"expected_remote_call_count": 38, **plan_body, "plan_sha256": _digest(plan_body), "approval": None},
            "artifacts": artifacts, "calls": calls,
            "final_review": {"status": "pending", "decision": None, "note": None, "reviewed_at": None},
            "events": [],
            "warnings": ["No automatic quality score or redraw.", "Technical success advances automatically.", "Planned locator center and deterministic aperture are not final click truth."],
        }
        _atomic(manifest_path, manifest)
        return manifest

    def _validate_config(self, config):
        if list(config.get("routes", {})) != list(ROUTES):
            raise ValueError("routes must be M0 and M1")
        if [item["id"] for item in config["destinations"]] != list(DESTINATIONS):
            raise ValueError("destination matrix mismatch")
        if any([scene["id"] for scene in item["scenes"]] != list(SCENES) for item in config["destinations"]):
            raise ValueError("each destination must contain A and B")

    def _import_g06(self, run_dir, config, source_run, artifacts):
        source_dir, source_manifest_path = self.paths(source_run)
        source = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        g06 = next(item for item in config["destinations"] if item["id"] == "G06")
        import_config = g06["environment_import"]
        if import_config["source_run_id"] != source_run:
            raise ValueError("G06 source run mismatch")
        expected = import_config["routes"]
        for route in ROUTES:
            candidate = source["base_candidates"][route]
            outputs = candidate["attempts"][-1]["outputs"]
            if len(outputs) != 1:
                raise ValueError(f"G06 {route} source output mismatch")
            record = outputs[0]
            source_path = _safe_relative(source_dir, record["path"])
            actual = ASSETS.sha256_file(source_path)
            if actual != record["sha256"] or actual != expected[route]["sha256"]:
                raise ValueError(f"G06 {route} hash mismatch")
            destination = run_dir / "artifacts" / "environments" / "G06" / f"{route}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            if ASSETS.sha256_file(destination) != actual:
                raise ValueError("G06 copy hash mismatch")
            artifacts[f"environment:G06:{route}"] = {
                "kind": "environment", "destination": "G06", "route": route,
                "path": destination.relative_to(run_dir).as_posix(), "sha256": actual,
                "size": record["size"], "origin": {"type": "imported_run_artifact", "run_id": source_run,
                "source_task_id": candidate["attempts"][-1]["task_id"], "source_path": record["path"], "source_sha256": actual},
            }

    def _build_calls(self, run_id, config, assets):
        destinations = {item["id"]: item for item in config["destinations"]}
        styles = {item["cell"]: item for item in assets["styles"]}
        compositions = {item["row"]: item for item in assets["compositions"]}
        calls: dict[str, Any] = {}
        ordinal = 0
        for destination_id in ("G02", "G03", "G08"):
            destination = destinations[destination_id]
            for route in ROUTES:
                ordinal += 1
                refs = [{"order": 1, "role": "style", "artifact_id": f"style:{destination['style_cell']}"}]
                if route == "M1":
                    refs.append({"order": 2, "role": "character_bottom", "artifact_id": "character:bottom"})
                call_id = f"environment:{destination_id}:{route}"
                calls[call_id] = self._call(call_id, ordinal, "environment", destination_id, route, None, [],
                    _environment_prompt(destination, styles[destination["style_cell"]], compositions[destination["composition_row"]], route), refs,
                    f"artifacts/environments/{destination_id}/{route}.png", run_id, config)
        for destination_id in DESTINATIONS:
            destination = destinations[destination_id]
            for route in ROUTES:
                for scene in destination["scenes"]:
                    ordinal += 1
                    call_id = f"locator:{destination_id}:{route}:{scene['id']}"
                    environment = f"environment:{destination_id}:{route}"
                    calls[call_id] = self._call(call_id, ordinal, "locator", destination_id, route, scene["id"], [environment],
                        _locator_prompt(destination, scene), [{"order": 1, "role": "route_environment", "artifact_id": environment}],
                        f"artifacts/locators/{destination_id}/{route}/{scene['id']}.png", run_id, config)
        for destination_id in DESTINATIONS:
            destination = destinations[destination_id]
            for route in ROUTES:
                for scene in destination["scenes"]:
                    ordinal += 1
                    call_id = f"final:{destination_id}:{route}:{scene['id']}"
                    aperture = f"aperture:{destination_id}:{route}:{scene['id']}"
                    locator = f"locator:{destination_id}:{route}:{scene['id']}"
                    calls[call_id] = self._call(call_id, ordinal, "final", destination_id, route, scene["id"], [locator],
                        _final_prompt(destination, scene), [{"order": 1, "role": "deterministic_aperture", "artifact_id": aperture}, {"order": 2, "role": "character_bottom", "artifact_id": "character:bottom"}],
                        f"artifacts/finals/{destination_id}/{route}/{scene['id']}.png", run_id, config)
        if len(calls) != 38:
            raise AssertionError(len(calls))
        return calls

    def _call(self, call_id, ordinal, phase, destination, route, scene, dependencies, prompt, references, output, run_id, config):
        spec = {"prompt": prompt, "references": references, "request": {"provider": "65535", **config["api"], "base_url": "https://task-api-1-cn.65535.space"},
                "output_path": output, "idem_key": f"issue12-{run_id}-{call_id.replace(':', '-').lower()}", "depends_on": dependencies}
        spec["snapshot_sha256"] = _digest(spec)
        return {"id": call_id, "ordinal": ordinal, "phase": phase, "destination": destination, "route": route, "scene": scene,
                "status": "pending", "blocked_by": [], "spec": spec, "attempt": None, "result": None}

    def preflight(self, run_id: str) -> dict[str, Any]:
        manifest = self.load(run_id)
        calls = manifest["calls"]
        counts = {phase: sum(call["phase"] == phase for call in calls.values()) for phase in ("environment", "locator", "final")}
        if counts != {"environment": 6, "locator": 16, "final": 16}:
            raise ValueError(f"call matrix mismatch: {counts}")
        unique_fields = ("idem_key", "output_path")
        for field in unique_fields:
            values = [call["spec"][field] for call in calls.values()]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {field}")
        if [call["ordinal"] for call in calls.values()] != list(range(1, 39)):
            raise ValueError("ordinal mismatch")
        for call in calls.values():
            if _digest({key: value for key, value in call["spec"].items() if key != "snapshot_sha256"}) != call["spec"]["snapshot_sha256"]:
                raise ValueError(f"snapshot mismatch: {call['id']}")
            self._validate_reference_policy(call)
        self._validate_plan(manifest)
        return {"run_id": run_id, "counts": counts, "imported_environments": 2, "plan_sha256": manifest["plan"]["plan_sha256"]}

    def _validate_reference_policy(self, call):
        roles = [item["role"] for item in call["spec"]["references"]]
        expected = {"environment": ["style"] if call["route"] == "M0" else ["style", "character_bottom"],
                    "locator": ["route_environment"], "final": ["deterministic_aperture", "character_bottom"]}[call["phase"]]
        if roles != expected:
            raise ValueError(f"reference policy mismatch: {call['id']}")

    def _validate_reference_identity(self, call):
        ids = [item["artifact_id"] for item in call["spec"]["references"]]
        if call["phase"] == "environment":
            expected = [f"style:{next(item['style_cell'] for item in self._active_config['destinations'] if item['id'] == call['destination'])}"]
            if call["route"] == "M1":
                expected.append("character:bottom")
        elif call["phase"] == "locator":
            expected = [f"environment:{call['destination']}:{call['route']}"]
        else:
            expected = [f"aperture:{call['destination']}:{call['route']}:{call['scene']}", "character:bottom"]
        if ids != expected:
            raise ValueError(f"reference identity mismatch: {call['id']}")

    def _expected_call_identities(self):
        identities = {}
        for destination in ("G02", "G03", "G08"):
            for route in ROUTES:
                identities[f"environment:{destination}:{route}"] = ("environment", destination, route, None)
        for phase in ("locator", "final"):
            for destination in DESTINATIONS:
                for route in ROUTES:
                    for scene in SCENES:
                        identities[f"{phase}:{destination}:{route}:{scene}"] = (phase, destination, route, scene)
        return identities

    def _validate_plan(self, manifest):
        self._active_config = manifest["config"]
        plan = manifest["plan"]
        expected_identities = self._expected_call_identities()
        if list(manifest["calls"]) != list(expected_identities):
            raise ValueError("call matrix identity mismatch")
        for call_id, call in manifest["calls"].items():
            expected = expected_identities[call_id]
            identity = (call["phase"], call["destination"], call["route"], call["scene"])
            if identity != expected:
                raise ValueError(f"call identity mismatch: {call_id}")
            if _snapshot(call) != call["spec"]["snapshot_sha256"]:
                raise ValueError(f"snapshot mismatch: {call_id}")
            self._validate_reference_policy(call)
            self._validate_reference_identity(call)
        body = {"ordered_call_ids": list(manifest["calls"]),
                "snapshot_sha256s": [call["spec"]["snapshot_sha256"] for call in manifest["calls"].values()],
                "imported_environment_sha256s": [manifest["artifacts"][f"environment:G06:{route}"]["sha256"] for route in ROUTES]}
        if _digest(body) != plan["plan_sha256"]:
            raise ValueError("plan digest mismatch")
        if plan["ordered_call_ids"] != body["ordered_call_ids"] or plan["snapshot_sha256s"] != body["snapshot_sha256s"]:
            raise ValueError("plan body mismatch")

    def approve_plan(self, run_id, plan_sha256):
        manifest = self.load(run_id)
        self._validate_plan(manifest)
        if manifest["plan"]["plan_sha256"] != plan_sha256:
            raise ValueError("plan approval mismatch")
        manifest["plan"]["approval"] = {"plan_sha256": plan_sha256, "approved_at": _now()}
        manifest["events"].append({"at": _now(), "event": "plan_approved"})
        self.save(run_id, manifest)

    def _resolve_references(self, run_dir, manifest, call):
        resolved = []
        for logical in call["spec"]["references"]:
            artifact_id = logical["artifact_id"]
            artifact = manifest["artifacts"].get(artifact_id)
            if not artifact:
                raise ValueError(f"missing artifact {artifact_id}")
            self._validate_artifact_producer(manifest, artifact_id, artifact)
            path = _safe_relative(run_dir, artifact["path"])
            if not path.is_file() or ASSETS.sha256_file(path) != artifact["sha256"]:
                raise ValueError(f"artifact hash mismatch {logical['artifact_id']}")
            resolved.append({**logical, "path": artifact["path"], "sha256": artifact["sha256"]})
        return resolved

    def _validate_artifact_producer(self, manifest, artifact_id, artifact):
        if artifact_id.startswith(("style:", "character:")):
            return
        if artifact_id.startswith("environment:G06:") and artifact.get("origin", {}).get("type") == "imported_run_artifact":
            return
        if artifact_id.startswith("aperture:"):
            parts = artifact_id.split(":")
            expected = f"locator:{parts[1]}:{parts[2]}:{parts[3]}"
            if artifact.get("derived_from_call_id") != expected:
                raise ValueError(f"aperture producer mismatch: {artifact_id}")
            producer = manifest["calls"].get(expected) or {}
            if producer.get("status") != "succeeded" or producer.get("result", {}).get("aperture_artifact_id") != artifact_id:
                raise ValueError(f"aperture result mismatch: {artifact_id}")
            return
        producer = manifest["calls"].get(artifact_id)
        if not producer or producer.get("status") != "succeeded":
            raise ValueError(f"artifact producer missing: {artifact_id}")
        outputs = producer.get("result", {}).get("outputs") or []
        if len(outputs) != 1 or outputs[0].get("path") != artifact.get("path") or outputs[0].get("sha256") != artifact.get("sha256"):
            raise ValueError(f"artifact producer result mismatch: {artifact_id}")
        if artifact.get("producer_call_id") != artifact_id:
            raise ValueError(f"artifact producer identity mismatch: {artifact_id}")

    def execute(self, run_id: str) -> dict[str, Any]:
        while True:
            manifest = self.load(run_id)
            self._validate_plan(manifest)
            approval = manifest["plan"].get("approval")
            if not approval or approval["plan_sha256"] != manifest["plan"]["plan_sha256"]:
                raise ValueError("approved plan required")
            self._propagate_blocked(manifest)
            self.save(run_id, manifest)
            running = [call for call in manifest["calls"].values() if call["status"] == "running"]
            if running:
                call = min(running, key=lambda item: item["ordinal"])
                self._finish_call(run_id, call["id"])
                if self.load(run_id)["calls"][call["id"]]["status"] == "running":
                    return self.status(run_id) | {"paused_on": call["id"]}
                continue
            unknown = [call for call in manifest["calls"].values() if call["status"] == "submission_unknown"]
            ready = [call for call in manifest["calls"].values() if call["status"] == "pending" and self._ready(manifest, call)]
            if ready:
                self._start_call(run_id, min(ready, key=lambda item: item["ordinal"])["id"])
                continue
            self._update_final_review(manifest)
            self.save(run_id, manifest)
            return self.status(run_id) | {"submission_unknown": [item["id"] for item in unknown]}

    def _ready(self, manifest, call):
        return all((dependency in manifest["artifacts"] or manifest["calls"].get(dependency, {}).get("status") == "succeeded") for dependency in call["spec"]["depends_on"])

    def _session(self):
        relay = _module("relay_full", self.root.parent / "pilot4mvp2" / "scripts" / "relay_async_image.py")
        namespace = argparse.Namespace(base_url=None, api_key=None)
        base, key = relay.resolve_config(namespace)
        session = relay.requests.Session(); session.headers.update({"Authorization": f"Bearer {key}"})
        return relay, session, base

    def _start_call(self, run_id, call_id):
        manifest = self.load(run_id); call = manifest["calls"][call_id]; run_dir = self.paths(run_id)[0]
        resolved = self._resolve_references(run_dir, manifest, call)
        if call["attempt"] is not None:
            raise ValueError("redraw/second attempt prohibited")
        relay, session, base = self._session()
        if base != call["spec"]["request"]["base_url"].rstrip("/"):
            raise ValueError("API base mismatch")
        call["attempt"] = {"attempt_number": 1, "started_at": _now(), "idem_key": call["spec"]["idem_key"], "task_id": None,
                           "submitted_at": None, "resolved_references": resolved, "resolved_request_sha256": _digest({"spec": call["spec"], "references": resolved}), "error": None}
        call["status"] = "submission_unknown"
        self.save(run_id, manifest)
        payload = {"kind": "image", "model": call["spec"]["request"]["model"], "input": {"prompt": call["spec"]["prompt"]["rendered"],
                   "size": call["spec"]["request"]["size"], "resolution": call["spec"]["request"]["resolution"], "quality": call["spec"]["request"]["quality"],
                   "n": 1, "image_urls": [relay.normalize_ref(str(_safe_relative(run_dir, item["path"]))) for item in resolved]}}
        def created(task_id, _task):
            current = self.load(run_id); current_call = current["calls"][call_id]
            current_call["attempt"]["task_id"] = task_id; current_call["attempt"]["submitted_at"] = _now(); current_call["status"] = "running"; self.save(run_id, current)
        result = relay.submit_task(session, base, payload, call["spec"]["idem_key"], on_created=created)
        if not result["ok"]:
            current = self.load(run_id); current_call = current["calls"][call_id]
            current_call["attempt"]["error"] = result["error"]
            current_call["status"] = "submission_unknown" if result["error"]["code"] == "submission_unknown" else "failed"
            self.save(run_id, current); return
        self._finish_call(run_id, call_id, relay, session, base)

    def _finish_call(self, run_id, call_id, relay=None, session=None, base=None):
        manifest = self.load(run_id); call = manifest["calls"][call_id]; attempt = call["attempt"]
        if not attempt or not attempt.get("task_id"):
            raise ValueError("running call missing task_id")
        if relay is None: relay, session, base = self._session()
        polled = relay.poll_task(session, base, attempt["task_id"], call["spec"]["request"]["timeout"])
        if not polled["ok"]:
            attempt["error"] = polled["error"]
            if not polled["error"].get("retryable"): call["status"] = "failed"
            self.save(run_id, manifest); return
        output = _safe_relative(self.paths(run_id)[0], call["spec"]["output_path"])
        downloaded = relay.download_results(session, polled["task"], attempt["task_id"], str(output), download_session=relay.requests.Session())
        if not downloaded["ok"]:
            attempt["error"] = downloaded["error"]
            if not downloaded["error"].get("retryable"): call["status"] = "failed"
            self.save(run_id, manifest); return
        if len(downloaded["saved"]) != 1:
            call["status"] = "failed"; attempt["error"] = {"code": "output_count_mismatch"}; self.save(run_id, manifest); return
        path = Path(downloaded["saved"][0]["path"]); artifact_id = call_id if call["phase"] == "environment" else call_id
        artifact = {
            "kind": call["phase"],
            "producer_call_id": call_id,
            "destination": call["destination"],
            "route": call["route"],
            "scene": call["scene"],
            "path": path.resolve().relative_to(self.paths(run_id)[0]).as_posix(),
            "sha256": ASSETS.sha256_file(path),
            "size": downloaded["saved"][0]["size"],
        }
        manifest["artifacts"][artifact_id] = artifact
        call["status"] = "succeeded"; call["result"] = {"task": relay.safe_task_metadata(polled["task"]), "outputs": [artifact], "finished_at": _now()}
        attempt["error"] = None
        if call["phase"] == "locator":
            try: self._derive_aperture(manifest, call)
            except Exception as error:
                call["status"] = "failed"; call["result"]["technical_error"] = {"code": "locator_processing_failed", "message": str(error)}
        self.save(run_id, manifest)

    def _derive_aperture(self, manifest, call):
        run_dir = self.paths(manifest["run_id"])[0]
        environment_id = f"environment:{call['destination']}:{call['route']}"
        environment = manifest["artifacts"][environment_id]; locator = manifest["artifacts"][call["id"]]
        measurement = BASE.detect_single_black_circle(_safe_relative(run_dir, environment["path"]), _safe_relative(run_dir, locator["path"]))
        aperture_id = f"aperture:{call['destination']}:{call['route']}:{call['scene']}"
        output = _safe_relative(
            run_dir,
            f"artifacts/apertures/{call['destination']}/{call['route']}/{call['scene']}.png",
        )
        aperture = BASE.draw_deterministic_aperture(
            _safe_relative(run_dir, environment["path"]),
            output,
            tuple(measurement["planned_locator_center"]),
            manifest["config"]["aperture"]["short_edge_ratio"],
        )
        aperture["path"] = output.relative_to(run_dir).as_posix()
        aperture["kind"] = "deterministic_aperture"
        aperture["measurement"] = measurement
        aperture["derived_from_call_id"] = call["id"]
        aperture["destination"] = call["destination"]
        aperture["route"] = call["route"]
        aperture["scene"] = call["scene"]
        manifest["artifacts"][aperture_id] = aperture; call["result"]["measurement"] = measurement; call["result"]["aperture_artifact_id"] = aperture_id

    def _propagate_blocked(self, manifest):
        changed = True
        while changed:
            changed = False
            for call in manifest["calls"].values():
                if call["status"] != "pending": continue
                blockers = [dep for dep in call["spec"]["depends_on"] if manifest["calls"].get(dep, {}).get("status") in {"failed", "blocked"}]
                if blockers: call["status"] = "blocked"; call["blocked_by"] = blockers; changed = True

    def _update_final_review(self, manifest):
        statuses = {call["status"] for call in manifest["calls"].values()}
        if statuses <= TERMINAL: manifest["final_review"]["status"] = "waiting_for_review"

    def attach_task(self, run_id, call_id, task_id, note):
        manifest = self.load(run_id); call = manifest["calls"][call_id]
        if call["status"] != "submission_unknown" or not note.strip(): raise ValueError("attach requires submission_unknown and note")
        call["attempt"]["task_id"] = task_id; call["attempt"]["attached_at"] = _now(); call["attempt"]["attach_note"] = note; call["status"] = "running"; self.save(run_id, manifest)

    def abandon_unknown(self, run_id, call_id, note):
        manifest = self.load(run_id); call = manifest["calls"][call_id]
        if call["status"] != "submission_unknown" or not note.strip(): raise ValueError("abandon requires submission_unknown and note")
        call["status"] = "failed"; call["attempt"]["error"] = {"code": "submission_abandoned", "message": note}; self._propagate_blocked(manifest); self.save(run_id, manifest)

    def review_run(self, run_id, decision, note):
        manifest = self.load(run_id)
        if manifest["final_review"]["status"] != "waiting_for_review" or decision not in {"accept", "reject"} or not note.strip(): raise ValueError("run is not ready for review")
        manifest["final_review"] = {"status": "reviewed", "decision": decision, "note": note, "reviewed_at": _now()}; self.save(run_id, manifest)

    def status(self, run_id):
        manifest = self.load(run_id); counts = {}
        for call in manifest["calls"].values(): counts[call["status"]] = counts.get(call["status"], 0) + 1
        return {"run_id": run_id, "counts": counts, "final_review": manifest["final_review"]["status"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-full"); prepare.add_argument("--config", type=Path, required=True); prepare.add_argument("--run-id", required=True); prepare.add_argument("--source-run", default="issue12-preflight-001")
    for command in ("preflight-all", "execute", "status", "report"):
        item = commands.add_parser(command); item.add_argument("--run", required=True)
    approve = commands.add_parser("approve-plan"); approve.add_argument("--run", required=True); approve.add_argument("--plan-sha256", required=True)
    attach = commands.add_parser("attach-task"); attach.add_argument("--run", required=True); attach.add_argument("--call-id", required=True); attach.add_argument("--task-id", required=True); attach.add_argument("--note", required=True)
    abandon = commands.add_parser("abandon-unknown"); abandon.add_argument("--run", required=True); abandon.add_argument("--call-id", required=True); abandon.add_argument("--note", required=True)
    review = commands.add_parser("review-run"); review.add_argument("--run", required=True); review.add_argument("--decision", choices=("accept", "reject"), required=True); review.add_argument("--note", required=True)
    args = parser.parse_args(); runner = FullRunner()
    if args.command == "prepare-full": result = runner.prepare(args.config, args.run_id, args.source_run); print(json.dumps({"run_id": result["run_id"], "plan_sha256": result["plan"]["plan_sha256"]}, ensure_ascii=False))
    elif args.command == "preflight-all": print(json.dumps(runner.preflight(args.run), ensure_ascii=False, indent=2))
    elif args.command == "approve-plan": runner.approve_plan(args.run, args.plan_sha256); print("plan approved")
    elif args.command == "execute": print(json.dumps(runner.execute(args.run), ensure_ascii=False, indent=2))
    elif args.command == "status": print(json.dumps(runner.status(args.run), ensure_ascii=False, indent=2))
    elif args.command == "attach-task": runner.attach_task(args.run, args.call_id, args.task_id, args.note); print("task attached")
    elif args.command == "abandon-unknown": runner.abandon_unknown(args.run, args.call_id, args.note); print("unknown submission abandoned")
    elif args.command == "review-run": runner.review_run(args.run, args.decision, args.note); print("review recorded")
    else:
        report = _module("issue12_report_full", ROOT / "scripts" / "issue12_report.py"); run_dir, manifest = runner.paths(args.run); output = run_dir / "evidence.html"; report.render_evidence(manifest, output); print(output)


if __name__ == "__main__":
    main()
