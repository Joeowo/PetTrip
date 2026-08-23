"""Single-step controller for the Issue 12 image experiment prototype."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageChops, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "issue12" / "runs"
STATES = {"pending", "running", "waiting_for_review", "continued", "stopped", "failed"}
LAYER_KEYS = [
    "fixed_boundaries",
    "destination_requirement",
    "style_description",
    "composition_prompt",
    "current_anchor_action",
]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    body = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    return _hash(body)


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def nearest_even(value: float) -> int:
    lower = math.floor(value / 2) * 2
    upper = lower + 2
    return lower if value - lower < upper - value else upper


def half_up(value: float) -> int:
    return math.floor(value + 0.5)


DEFAULT_LOCATOR_POLICY = {
    "algorithm": "black-filled-ellipse/v1",
    "candidate_m_max": 20,
    "minimum_area": 500,
    "minimum_bbox_side": 20,
    "aspect_min": 0.25,
    "aspect_max": 4.0,
    "fill_min": 0.70,
    "fill_max": 0.90,
    "reject_canvas_edge": True,
    "ellipse_m_p90_max": 12,
    "ellipse_chroma_p90_max": 6,
    "ellipse_q20_min": 0.94,
    "delta_y_mean_min": 40,
}


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def detect_black_locator(
    environment_path: Path,
    locator_path: Path,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select the strongest qualified black filled circle/ellipse marker."""
    settings = {**DEFAULT_LOCATOR_POLICY, **(policy or {})}
    with Image.open(environment_path) as source, Image.open(locator_path) as locator:
        environment = source.convert("RGB")
        marked = locator.convert("RGB")
    if environment.size != marked.size:
        raise ValueError("dimension_mismatch")
    width, height = marked.size
    pixels = marked.load()
    remaining = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if max(pixels[x, y]) <= settings["candidate_m_max"]
    }
    components = []
    while remaining:
        seed = remaining.pop()
        component = {seed}
        stack = [seed]
        while stack:
            x, y = stack.pop()
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    point = (nx, ny)
                    if point in remaining:
                        remaining.remove(point)
                        component.add(point)
                        stack.append(point)
        components.append(component)

    candidates = []
    rejection_counts: dict[str, int] = {}
    environment_pixels = environment.load()
    for index, component in enumerate(components):
        xs = [point[0] for point in component]
        ys = [point[1] for point in component]
        area = len(component)
        left, top, right, bottom = min(xs), min(ys), max(xs) + 1, max(ys) + 1
        bbox_width, bbox_height = right - left, bottom - top
        aspect = max(bbox_width, bbox_height) / min(bbox_width, bbox_height)
        fill = area / (bbox_width * bbox_height)
        touches_edge = left == 0 or top == 0 or right == width or bottom == height
        reasons = []
        if area < settings["minimum_area"]:
            reasons.append("area")
        if min(bbox_width, bbox_height) < settings["minimum_bbox_side"]:
            reasons.append("bbox_side")
        if not settings["aspect_min"] <= 1 / aspect <= settings["aspect_max"]:
            reasons.append("aspect")
        if not settings["fill_min"] <= fill <= settings["fill_max"]:
            reasons.append("fill")
        if settings["reject_canvas_edge"] and touches_edge:
            reasons.append("canvas_edge")

        ellipse_points = []
        center_x = (left + right - 1) / 2
        center_y = (top + bottom - 1) / 2
        radius_x = max((bbox_width - 1) / 2, 0.5)
        radius_y = max((bbox_height - 1) / 2, 0.5)
        for y in range(top, bottom):
            for x in range(left, right):
                normalized = ((x - center_x) / radius_x) ** 2 + ((y - center_y) / radius_y) ** 2
                if normalized <= 1:
                    ellipse_points.append((x, y))
        if not ellipse_points:
            ellipse_points = list(component)
            reasons.append("ellipse_empty")
        marker_max = [max(pixels[point]) for point in ellipse_points]
        chroma = [max(pixels[point]) - min(pixels[point]) for point in ellipse_points]
        locator_luminance = [
            0.2126 * pixels[point][0] + 0.7152 * pixels[point][1] + 0.0722 * pixels[point][2]
            for point in ellipse_points
        ]
        environment_luminance = [
            0.2126 * environment_pixels[point][0]
            + 0.7152 * environment_pixels[point][1]
            + 0.0722 * environment_pixels[point][2]
            for point in ellipse_points
        ]
        m_p90 = _percentile(marker_max, 0.9)
        chroma_p90 = _percentile(chroma, 0.9)
        q20 = sum(value <= 20 for value in marker_max) / len(marker_max)
        delta_y_mean = sum(
            before - after
            for before, after in zip(environment_luminance, locator_luminance)
        ) / len(locator_luminance)
        luminance_median = _percentile(locator_luminance, 0.5)
        luminance_mad = _percentile(
            [abs(value - luminance_median) for value in locator_luminance], 0.5
        )
        luminance_iqr = _percentile(locator_luminance, 0.75) - _percentile(
            locator_luminance, 0.25
        )
        if m_p90 > settings["ellipse_m_p90_max"]:
            reasons.append("black_depth")
        if chroma_p90 > settings["ellipse_chroma_p90_max"]:
            reasons.append("chroma")
        if q20 < settings["ellipse_q20_min"]:
            reasons.append("black_coverage")
        if delta_y_mean < settings["delta_y_mean_min"]:
            reasons.append("darkening")
        ellipse_area = len(ellipse_points)
        ellipse_coverage = area / ellipse_area if ellipse_area else 0
        if not 0.70 <= ellipse_coverage <= 1.15:
            reasons.append("ellipse_coverage")

        score = (
            0.25 * max(0, min(1, (20 - m_p90) / 12))
            + 0.25 * max(0, min(1, (q20 - 0.80) / 0.18))
            + 0.15 * max(0, min(1, (10 - chroma_p90) / 8))
            + 0.10 * max(0, min(1, (8 - luminance_iqr) / 6))
            + 0.15 * max(0, min(1, (delta_y_mean - 20) / 50))
            + 0.10 * max(0, min(1, (fill - 0.55) / 0.22))
        )
        candidate = {
            "component_index": index,
            "area": area,
            "bbox": [left, top, right, bottom],
            "bbox_width": bbox_width,
            "bbox_height": bbox_height,
            "aspect_ratio": round(aspect, 6),
            "fill_ratio": round(fill, 6),
            "touches_canvas_edge": touches_edge,
            "ellipse": {
                "center": [center_x, center_y],
                "radii": [radius_x, radius_y],
                "coverage": round(ellipse_coverage, 6),
            },
            "max_channel_p90": round(m_p90, 4),
            "chroma_p90": round(chroma_p90, 4),
            "fraction_max_channel_le_20": round(q20, 6),
            "delta_luminance_mean": round(delta_y_mean, 4),
            "luminance_mad": round(luminance_mad, 4),
            "luminance_iqr": round(luminance_iqr, 4),
            "score": round(score, 8),
            "accepted": not reasons,
            "rejection_reasons": reasons,
        }
        candidates.append(candidate)
        for reason in set(reasons):
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    accepted = [candidate for candidate in candidates if candidate["accepted"]]
    if not accepted:
        raise ValueError(
            "no_plausible_black_marker: "
            + json.dumps(rejection_counts, sort_keys=True)
        )
    selected = sorted(
        accepted,
        key=lambda item: (
            -item["score"],
            -item["fraction_max_channel_le_20"],
            item["max_channel_p90"],
            -item["area"],
            item["bbox"][1],
            item["bbox"][0],
            item["bbox"][3],
            item["bbox"][2],
        ),
    )[0]
    center_float = selected["ellipse"]["center"]
    return {
        "algorithm": settings["algorithm"],
        "policy": settings,
        "raw_component_count": len(components),
        "qualified_candidate_count": len(accepted),
        "rejection_counts": rejection_counts,
        "selected_candidate": selected,
        "candidate_diagnostics": sorted(
            candidates, key=lambda item: (-item["accepted"], -item["score"])
        )[:20],
        "planned_locator_center_float": center_float,
        "planned_locator_center": [half_up(center_float[0]), half_up(center_float[1])],
        "bbox": selected["bbox"],
        "area": selected["area"],
    }


def detect_single_black_circle(
    clean_path: Path,
    locator_path: Path,
    black_max: int = 20,
    difference_min: int = 20,
    minimum_area: int = 25,
) -> dict[str, Any]:
    """Find exactly one changed near-black 8-connected component."""
    with Image.open(clean_path) as source, Image.open(locator_path) as locator:
        clean = source.convert("RGB")
        edited = locator.convert("RGB")
    if clean.size != edited.size:
        raise ValueError("locator dimensions differ from environment base")
    difference = ImageChops.difference(clean, edited)
    width, height = clean.size
    candidates = set()
    clean_pixels = difference.load()
    edited_pixels = edited.load()
    for y in range(height):
        for x in range(width):
            if max(clean_pixels[x, y]) > difference_min and max(edited_pixels[x, y]) <= black_max:
                candidates.add((x, y))
    components = []
    while candidates:
        seed = candidates.pop()
        component = {seed}
        stack = [seed]
        while stack:
            x, y = stack.pop()
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    point = (nx, ny)
                    if point in candidates:
                        candidates.remove(point)
                        component.add(point)
                        stack.append(point)
        if len(component) >= minimum_area:
            components.append(component)
    if len(components) != 1:
        raise ValueError(f"expected one black component, found {len(components)}")
    points = components[0]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    center_float = [sum(xs) / len(xs), sum(ys) / len(ys)]
    center = [half_up(center_float[0]), half_up(center_float[1])]
    bbox = [min(xs), min(ys), max(xs) + 1, max(ys) + 1]
    if bbox[0] == 0 or bbox[1] == 0 or bbox[2] == width or bbox[3] == height:
        raise ValueError("black component touches canvas edge")
    return {
        "planned_locator_center_float": center_float,
        "planned_locator_center": center,
        "bbox": bbox,
        "area": len(points),
    }


def draw_deterministic_aperture(
    environment_path: Path,
    output_path: Path,
    center: tuple[int, int],
    short_edge_ratio: float = 0.14,
) -> dict[str, Any]:
    with Image.open(environment_path) as source:
        image = source.convert("RGB")
    if not 0 < short_edge_ratio < 1:
        raise ValueError("short_edge_ratio must be between zero and one")
    diameter = nearest_even(min(image.size) * short_edge_ratio)
    radius = diameter // 2
    x, y = center
    box = (x - radius, y - radius, x + radius - 1, y + radius - 1)
    if box[0] < 0 or box[1] < 0 or box[2] >= image.width or box[3] >= image.height:
        raise ValueError("deterministic aperture exceeds canvas")
    ImageDraw.Draw(image).ellipse(box, fill="black")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return {
        "path": output_path.as_posix(),
        "center": [x, y],
        "radius": radius,
        "diameter": diameter,
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "truth_boundary": "planned aperture; not final click truth",
    }


def build_prompt_layers(
    destination: dict[str, Any],
    style: dict[str, Any],
    composition: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, str]:
    role_note = (
        "参考图2仅用于理解固定宠物形象的小尺寸比例和活动空间；不得在环境中生成任何宠物内容。"
        if variant["include_character_reference"]
        else "不输入宠物参考图；按场景语义预留两个小尺寸宠物可活动区域。"
    )
    layers = {
        "fixed_boundaries": (
            "生成16:9横版纯环境母图，手绘2D、自然克制、非摄影。固定镜头和单一主光方向；"
            "不含宠物、人物、宠物局部、剪影、雕塑、玩偶、壁画、黑圈、UI、文字、Logo或水印；"
            "不得复刻参考作品的具体角色、建筑、道具或剧情。"
        ),
        "destination_requirement": destination["scene_requirement"],
        "style_description": style["description"],
        "composition_prompt": composition["prompt"],
        "current_anchor_action": (
            f"预留两个空间分离、可站立、避开边缘和主地标的视觉锚点："
            f"{destination['scenes'][0]['anchor']}；{destination['scenes'][1]['anchor']}。{role_note}"
        ),
    }
    if list(layers) != LAYER_KEYS:
        raise AssertionError("prompt layer order changed")
    return layers


def render_prompt(layers: dict[str, str]) -> str:
    return "\n\n".join(f"[{index}] {layers[key]}" for index, key in enumerate(LAYER_KEYS, 1))


class ExperimentController:
    def __init__(self, root: Path = ROOT, assets_module: Any | None = None):
        self.root = root
        self.runs = root / "issue12" / "runs"
        self.assets_module = assets_module or _load_module(
            "issue12_assets", ROOT / "scripts" / "issue12_assets.py"
        )

    def _paths(self, run_id: str) -> tuple[Path, Path]:
        if not run_id or any(part in run_id for part in ("/", "\\", "..")):
            raise ValueError("invalid run_id")
        run_dir = self.runs / run_id
        return run_dir, run_dir / "manifest.json"

    def load(self, run_id: str) -> dict[str, Any]:
        return json.loads(self._paths(run_id)[1].read_text(encoding="utf-8"))

    def save(self, run_id: str, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = _now()
        _atomic_write(self._paths(run_id)[1], manifest)

    def prepare(self, config_path: Path, run_id: str) -> dict[str, Any]:
        run_dir, manifest_path = self._paths(run_id)
        if manifest_path.exists():
            raise FileExistsError(f"run already exists: {run_id}")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assets = self.assets_module.prepare_assets(config_path, run_dir)
        scene_steps = (
            "environment_base",
            "semantic_locator",
            "scan_planned_center",
            "draw_deterministic_aperture",
            "final_pet_scene",
        )
        workflows = {
            destination["id"]: {
                "shared_environment": {"path": None, "sha256": None},
                "scenes": {
                    scene["id"]: {
                        "anchor": scene["anchor"],
                        "action": scene["action"],
                        "steps": {
                            step: {
                                "status": "pending",
                                "attempts": [],
                                "review": None,
                            }
                            for step in scene_steps
                        },
                    }
                    for scene in destination["scenes"]
                },
            }
            for destination in config["destinations"]
        }
        manifest = {
            "schema_version": "issue12-run/0.1",
            "run_id": run_id,
            "created_at": _now(),
            "updated_at": _now(),
            "config": config,
            "assets": assets,
            "base_candidates": {
                "M0": {"status": "pending", "snapshot": None, "approval": None, "attempts": []},
                "M1": {"status": "pending", "snapshot": None, "approval": None, "attempts": []},
            },
            "workflows": workflows,
            "events": [],
            "warnings": [
                "No automatic quality score or redraw.",
                "Planned locator center and deterministic aperture are not final click truth.",
            ],
        }
        _atomic_write(manifest_path, manifest)
        return manifest

    def preflight(self, run_id: str, variant_id: str) -> dict[str, Any]:
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        if candidate["status"] != "pending":
            raise ValueError("preflight requires pending state")
        config = manifest["config"]
        destination = next(item for item in config["destinations"] if item["id"] == "G06")
        variant = next(item for item in destination["base_variants"] if item["id"] == variant_id)
        style_asset = next(item for item in manifest["assets"]["styles"] if item["cell"] == "E12")
        style_description = {
            "description": style_asset["description"],
            "source_row": style_asset["row"],
        }
        composition = next(
            item for item in manifest["assets"]["compositions"] if item["row"] == destination["composition_row"]
        )
        layers = build_prompt_layers(destination, style_description, composition, variant)
        references = [
            {
                "order": 1,
                "role": "style",
                "cell": style_asset["cell"],
                "path": style_asset["path"],
                "sha256": style_asset["output_sha256"],
                "width": style_asset["width"],
                "height": style_asset["height"],
            }
        ]
        if variant["include_character_reference"]:
            bottom = manifest["assets"]["character"]["bottom"]
            references.append({"order": 2, "role": "character_scale_only", **bottom})
        forbidden = {
            manifest["assets"]["character"]["source"]["sha256"],
            manifest["assets"]["character"]["top"]["sha256"],
        }
        if any(item["sha256"] in forbidden for item in references):
            raise ValueError("source or top character sheet leaked into request")
        rendered = render_prompt(layers)
        request = {
            "provider": "65535",
            **config["api"],
            "base_url": "https://task-api-1-cn.65535.space",
        }
        snapshot_body = {
            "variant": variant_id,
            "prompt": {"layers": layers, "rendered": rendered, "sha256": hashlib.sha256(rendered.encode()).hexdigest()},
            "references": references,
            "request": request,
            "idem_key": f"issue12-{run_id}-g06-{variant_id.lower()}-environment-base",
        }
        snapshot_body["snapshot_sha256"] = _hash(snapshot_body)
        candidate["snapshot"] = snapshot_body
        candidate["approval"] = None
        manifest["events"].append({"at": _now(), "variant": variant_id, "event": "preflight"})
        self.save(run_id, manifest)
        return snapshot_body

    def approve_call(self, run_id: str, variant_id: str, snapshot_sha256: str) -> None:
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        snapshot = candidate["snapshot"]
        if (
            not snapshot
            or snapshot["snapshot_sha256"] != snapshot_sha256
            or _snapshot_digest(snapshot) != snapshot_sha256
        ):
            raise ValueError("snapshot approval hash mismatch")
        candidate["approval"] = {"snapshot_sha256": snapshot_sha256, "approved_at": _now()}
        manifest["events"].append({"at": _now(), "variant": variant_id, "event": "call_approved"})
        self.save(run_id, manifest)

    def assert_start_allowed(self, run_id: str, variant_id: str) -> dict[str, Any]:
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        snapshot = candidate.get("snapshot")
        approval = candidate.get("approval")
        if candidate["status"] != "pending" or not snapshot or not approval:
            raise ValueError("call is not approved and pending")
        if (
            approval["snapshot_sha256"] != snapshot["snapshot_sha256"]
            or _snapshot_digest(snapshot) != snapshot["snapshot_sha256"]
        ):
            raise ValueError("approval no longer matches snapshot")
        run_dir = self._paths(run_id)[0].resolve()
        for reference in snapshot["references"]:
            relative = Path(reference["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"reference path escapes run: {reference['path']}")
            path = (run_dir / relative).resolve()
            if run_dir not in path.parents:
                raise ValueError(f"reference path escapes run: {reference['path']}")
            if self.assets_module.sha256_file(path) != reference["sha256"]:
                raise ValueError(f"reference changed: {reference['path']}")
        return snapshot

    def _relay(self):
        return _load_module(
            "relay_async_image",
            self.root.parent / "pilot4mvp2" / "scripts" / "relay_async_image.py",
        )

    def _session(self, relay):
        namespace = argparse.Namespace(base_url=None, api_key=None)
        base, key = relay.resolve_config(namespace)
        session = relay.requests.Session()
        session.headers.update({"Authorization": f"Bearer {key}"})
        return relay, session, base

    def start(self, run_id: str, variant_id: str) -> dict[str, Any]:
        """Submit one approved candidate; this is the only new-task network entry."""
        snapshot = self.assert_start_allowed(run_id, variant_id)
        relay = self._relay()
        relay, session, base = self._session(relay)
        if base != snapshot["request"]["base_url"].rstrip("/"):
            raise ValueError("resolved API base does not match approved snapshot")
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        candidate["status"] = "running"
        attempt = {
            "attempt": 1,
            "started_at": _now(),
            "idem_key": snapshot["idem_key"],
            "task_id": None,
            "error": None,
            "outputs": [],
        }
        candidate["attempts"].append(attempt)
        self.save(run_id, manifest)

        run_dir = self._paths(run_id)[0]
        task_input = {
            "prompt": snapshot["prompt"]["rendered"],
            "size": snapshot["request"]["size"],
            "resolution": snapshot["request"]["resolution"],
            "quality": snapshot["request"]["quality"],
            "n": snapshot["request"]["n"],
            "image_urls": [
                relay.normalize_ref(str(run_dir / item["path"]))
                for item in snapshot["references"]
            ],
        }
        payload = {
            "kind": "image",
            "model": snapshot["request"]["model"],
            "input": task_input,
        }
        def persist_task_id(task_id, _task):
            current = self.load(run_id)
            current_attempt = current["base_candidates"][variant_id]["attempts"][-1]
            current_attempt["task_id"] = task_id
            current_attempt["submitted_at"] = _now()
            self.save(run_id, current)

        submitted = relay.submit_task(
            session,
            base,
            payload,
            snapshot["idem_key"],
            on_created=persist_task_id,
        )
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        attempt = candidate["attempts"][-1]
        if not submitted["ok"]:
            candidate["status"] = "failed"
            attempt["error"] = submitted["error"]
            self.save(run_id, manifest)
            return submitted
        return self._finish_remote(run_id, variant_id, relay, session, base)

    def resume(self, run_id: str, variant_id: str) -> dict[str, Any]:
        """Resume polling an already persisted task_id; never submits a task."""
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        if candidate["status"] != "running" or not candidate["attempts"]:
            raise ValueError("resume requires a running attempt")
        if not candidate["attempts"][-1].get("task_id"):
            raise ValueError("resume requires a persisted task_id")
        relay = self._relay()
        relay, session, base = self._session(relay)
        return self._finish_remote(run_id, variant_id, relay, session, base)

    def _finish_remote(self, run_id, variant_id, relay, session, base):
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        attempt = candidate["attempts"][-1]
        task_id = attempt["task_id"]
        timeout = candidate["snapshot"]["request"]["timeout"]
        polled = relay.poll_task(session, base, task_id, timeout)
        if not polled["ok"]:
            attempt["error"] = polled["error"]
            if not polled["error"].get("retryable"):
                candidate["status"] = "failed"
            self.save(run_id, manifest)
            return polled
        output = self._paths(run_id)[0] / "base_candidates" / variant_id / "environment-base.png"
        downloaded = relay.download_results(
            session,
            polled["task"],
            task_id,
            str(output),
            download_session=relay.requests.Session(),
        )
        if not downloaded["ok"]:
            attempt["error"] = downloaded["error"]
            candidate["status"] = "failed"
            self.save(run_id, manifest)
            return downloaded
        for item in downloaded["saved"]:
            path = Path(item["path"])
            attempt["outputs"].append(
                {
                    "path": path.resolve().relative_to(self._paths(run_id)[0]).as_posix(),
                    "sha256": self.assets_module.sha256_file(path),
                    "size": item["size"],
                }
            )
        attempt["finished_at"] = _now()
        candidate["status"] = "waiting_for_review"
        self.save(run_id, manifest)
        return downloaded

    def select_base_candidate(
        self, run_id: str, variant_id: str, note: str
    ) -> dict[str, Any]:
        """Bind one reviewed G06 candidate as the immutable shared environment."""
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        if candidate["status"] != "continued" or not candidate["attempts"]:
            raise ValueError("base candidate must be reviewed and continued")
        outputs = candidate["attempts"][-1].get("outputs") or []
        if len(outputs) != 1:
            raise ValueError("base candidate must have exactly one output")
        shared = {
            "variant": variant_id,
            "path": outputs[0]["path"],
            "sha256": outputs[0]["sha256"],
            "selected_at": _now(),
            "note": note,
        }
        manifest["workflows"]["G06"]["shared_environment"] = shared
        for scene in manifest["workflows"]["G06"]["scenes"].values():
            step = scene["steps"]["environment_base"]
            step["status"] = "continued"
            step["outputs"] = [shared]
            step["review"] = {"decision": "continue", "note": note}
        manifest["events"].append(
            {"at": _now(), "variant": variant_id, "event": "shared_environment_selected"}
        )
        self.save(run_id, manifest)
        return shared

    def process_locator_output(
        self,
        run_id: str,
        destination_id: str,
        scene_id: str,
        locator_path: Path,
    ) -> dict[str, Any]:
        """Run deterministic steps 3 and 4 after a reviewed locator is available."""
        manifest = self.load(run_id)
        workflow = manifest["workflows"][destination_id]
        shared = workflow["shared_environment"]
        if not shared.get("path") or not shared.get("sha256"):
            raise ValueError("shared environment is not selected")
        scene = workflow["scenes"][scene_id]
        locator_step = scene["steps"]["semantic_locator"]
        if locator_step["status"] != "continued":
            raise ValueError("semantic locator must be reviewed and continued")
        run_dir = self._paths(run_id)[0].resolve()
        environment_path = (run_dir / shared["path"]).resolve()
        locator_path = locator_path.resolve()
        for path in (environment_path, locator_path):
            if run_dir not in path.parents:
                raise ValueError("workflow asset escapes run directory")
        if self.assets_module.sha256_file(environment_path) != shared["sha256"]:
            raise ValueError("shared environment hash changed")
        measurement = detect_single_black_circle(environment_path, locator_path)
        scan_step = scene["steps"]["scan_planned_center"]
        scan_step["status"] = "waiting_for_review"
        scan_step["measurement"] = measurement
        aperture_path = (
            run_dir
            / "workflows"
            / destination_id
            / scene_id
            / "deterministic-aperture.png"
        )
        aperture = draw_deterministic_aperture(
            environment_path,
            aperture_path,
            tuple(measurement["planned_locator_center"]),
        )
        aperture["path"] = aperture_path.relative_to(run_dir).as_posix()
        aperture_step = scene["steps"]["draw_deterministic_aperture"]
        aperture_step["status"] = "waiting_for_review"
        aperture_step["outputs"] = [aperture]
        manifest["events"].append(
            {
                "at": _now(),
                "destination": destination_id,
                "scene": scene_id,
                "event": "deterministic_locator_processed",
            }
        )
        self.save(run_id, manifest)
        return {"measurement": measurement, "aperture": aperture}

    def review(self, run_id: str, variant_id: str, decision: str, note: str) -> None:
        if decision not in {"continue", "stop"} or not note.strip():
            raise ValueError("review requires continue/stop and a note")
        manifest = self.load(run_id)
        candidate = manifest["base_candidates"][variant_id]
        if candidate["status"] != "waiting_for_review":
            raise ValueError("review requires waiting_for_review state")
        candidate["status"] = "continued" if decision == "continue" else "stopped"
        candidate["review"] = {"decision": decision, "note": note, "reviewed_at": _now()}
        self.save(run_id, manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--run-id", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--run", required=True)
    preflight.add_argument("--variant", choices=("M0", "M1"), required=True)
    approve = subparsers.add_parser("approve-call")
    approve.add_argument("--run", required=True)
    approve.add_argument("--variant", choices=("M0", "M1"), required=True)
    approve.add_argument("--snapshot-sha256", required=True)
    for command in ("start", "resume"):
        remote = subparsers.add_parser(command)
        remote.add_argument("--run", required=True)
        remote.add_argument("--variant", choices=("M0", "M1"), required=True)
    review = subparsers.add_parser("review")
    review.add_argument("--run", required=True)
    review.add_argument("--variant", choices=("M0", "M1"), required=True)
    review.add_argument("--decision", choices=("continue", "stop"), required=True)
    review.add_argument("--note", required=True)
    select_base = subparsers.add_parser("select-base")
    select_base.add_argument("--run", required=True)
    select_base.add_argument("--variant", choices=("M0", "M1"), required=True)
    select_base.add_argument("--note", required=True)
    locator = subparsers.add_parser("process-locator")
    locator.add_argument("--run", required=True)
    locator.add_argument("--destination", required=True)
    locator.add_argument("--scene", choices=("A", "B"), required=True)
    locator.add_argument("--locator", type=Path, required=True)
    report = subparsers.add_parser("report")
    report.add_argument("--run", required=True)
    args = parser.parse_args()
    controller = ExperimentController()
    if args.command == "prepare":
        result = controller.prepare(args.config, args.run_id)
        print(json.dumps({"run_id": result["run_id"], "status": "prepared"}, ensure_ascii=False))
    elif args.command == "preflight":
        print(json.dumps(controller.preflight(args.run, args.variant), ensure_ascii=False, indent=2))
    elif args.command == "approve-call":
        controller.approve_call(args.run, args.variant, args.snapshot_sha256)
        print("approval recorded; no API call was made")
    elif args.command == "start":
        print(json.dumps(controller.start(args.run, args.variant), ensure_ascii=False, indent=2))
    elif args.command == "resume":
        print(json.dumps(controller.resume(args.run, args.variant), ensure_ascii=False, indent=2))
    elif args.command == "review":
        controller.review(args.run, args.variant, args.decision, args.note)
        print("review recorded; no API call was made")
    elif args.command == "select-base":
        print(
            json.dumps(
                controller.select_base_candidate(args.run, args.variant, args.note),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "process-locator":
        print(
            json.dumps(
                controller.process_locator_output(
                    args.run, args.destination, args.scene, args.locator
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        report_module = _load_module("issue12_report", ROOT / "scripts" / "issue12_report.py")
        run_dir, manifest_path = controller._paths(args.run)
        output_path = run_dir / "evidence.html"
        report_module.render_evidence(manifest_path, output_path)
        print(output_path)


if __name__ == "__main__":
    main()
