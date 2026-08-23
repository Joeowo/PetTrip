"""Measure generated masks and build review contact sheets."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "experiment-manifest.json"
REVIEWS_DIR = ROOT / "reviews"
RATINGS_PATH = REVIEWS_DIR / "manual-review-ratings.json"
TARGET_DIAMETER = 108.0


def load_rgb(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot decode image: {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def find_generated_mask(scene: np.ndarray, mask_output: np.ndarray) -> dict:
    changed = np.max(cv2.absdiff(scene, mask_output), axis=2) > 20
    near_black = np.max(mask_output, axis=2) <= 15
    candidate = (changed & near_black).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
    if count <= 1:
        return {"detected": False, "reason": "no changed near-black component"}

    largest_label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    component = (labels == largest_label).astype(np.uint8)
    ys, xs = np.nonzero(component)
    points = np.column_stack((xs, ys)).astype(np.float32)
    (center_x, center_y), enclosing_radius = cv2.minEnclosingCircle(points)
    area = int(component.sum())
    equivalent_diameter = 2.0 * math.sqrt(area / math.pi)
    x = int(stats[largest_label, cv2.CC_STAT_LEFT])
    y = int(stats[largest_label, cv2.CC_STAT_TOP])
    width = int(stats[largest_label, cv2.CC_STAT_WIDTH])
    height = int(stats[largest_label, cv2.CC_STAT_HEIGHT])

    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
    circularity = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter else 0.0
    return {
        "detected": True,
        "center_px": [round(center_x, 2), round(center_y, 2)],
        "center_normalized_top_left": [
            round(center_x / scene.shape[1], 6),
            round(center_y / scene.shape[0], 6),
        ],
        "bbox_px": [x, y, width, height],
        "changed_black_area_px": area,
        "equivalent_diameter_px": round(equivalent_diameter, 2),
        "enclosing_diameter_px": round(enclosing_radius * 2.0, 2),
        "diameter_error_px": round(equivalent_diameter - TARGET_DIAMETER, 2),
        "circularity": round(circularity, 4),
        "component": component,
    }


def outside_difference(
    original: np.ndarray,
    edited: np.ndarray,
    measurement: dict,
    margin_px: int = 12,
) -> dict:
    exclusion = np.zeros(original.shape[:2], dtype=np.uint8)
    if measurement.get("detected"):
        center_x, center_y = measurement["center_px"]
        radius = measurement["enclosing_diameter_px"] / 2.0 + margin_px
        cv2.circle(
            exclusion,
            (round(center_x), round(center_y)),
            round(radius),
            255,
            -1,
        )
    outside = exclusion == 0
    difference = cv2.absdiff(original, edited).astype(np.float32)
    pixel_max = np.max(difference, axis=2)
    channel_values = difference[outside]
    return {
        "mean_absolute_channel_difference": round(float(channel_values.mean()), 4),
        "pixels_changed_over_10_ratio": round(float((pixel_max[outside] > 10).mean()), 6),
        "pixels_changed_over_30_ratio": round(float((pixel_max[outside] > 30).mean()), 6),
    }


def annotation_image(path: Path, measurement: dict) -> Image.Image:
    image = Image.open(path).convert("RGB")
    if measurement.get("detected"):
        draw = ImageDraw.Draw(image)
        center_x, center_y = measurement["center_px"]
        radius = measurement["enclosing_diameter_px"] / 2.0
        draw.ellipse(
            (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
            outline=(255, 40, 40),
            width=5,
        )
        draw.line((center_x - 10, center_y, center_x + 10, center_y), fill=(255, 40, 40), width=3)
        draw.line((center_x, center_y - 10, center_x, center_y + 10), fill=(255, 40, 40), width=3)
    return image


def fit_tile(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    tile = Image.new("RGB", size, "white")
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    x = (size[0] - copy.width) // 2
    y = (size[1] - copy.height) // 2
    tile.paste(copy, (x, y))
    return tile


def add_label(image: Image.Image, label: str, height: int = 42) -> Image.Image:
    result = Image.new("RGB", (image.width, image.height + height), "white")
    result.paste(image, (0, height))
    ImageDraw.Draw(result).text((10, 11), label, fill="black", font=ImageFont.load_default())
    return result


def build_group_sheet(group_id: str, entries: list[dict], measurements: dict) -> None:
    tile_size = (688, 384)
    scene_path = ROOT / entries[0]["scene"]
    cells = [add_label(fit_tile(Image.open(scene_path).convert("RGB"), tile_size), f"{group_id} scene")]
    blank = Image.new("RGB", tile_size, "white")
    cells.append(add_label(blank, "comparison order: mask then character"))

    for entry in entries:
        item = measurements[entry["id"]]
        mask = annotation_image(ROOT / entry["mask_output"], item["mask"])
        diameter = item["mask"].get("equivalent_diameter_px")
        center = item["mask"].get("center_px")
        cells.append(
            add_label(
                fit_tile(mask, tile_size),
                f"{entry['id']} MASK d={diameter}px center={center}",
            )
        )
        character = Image.open(ROOT / entry["character_output"]).convert("RGB")
        cells.append(add_label(fit_tile(character, tile_size), f"{entry['id']} CHARACTER"))

    rows = math.ceil(len(cells) / 2)
    cell_height = tile_size[1] + 42
    sheet = Image.new("RGB", (tile_size[0] * 2, cell_height * rows), (232, 232, 232))
    for index, cell in enumerate(cells):
        sheet.paste(cell, ((index % 2) * tile_size[0], (index // 2) * cell_height))
    sheet.save(REVIEWS_DIR / f"{group_id.lower()}-review-sheet.jpg", quality=91)


def write_manual_review_template(experiments: list[dict], measurements: dict) -> None:
    ratings = {}
    if RATINGS_PATH.is_file():
        ratings = json.loads(RATINGS_PATH.read_text(encoding="utf-8"))["ratings"]
    fields = [
        "experiment_id",
        "group_id",
        "element_id",
        "round",
        "target",
        "mask_position_score_1_5",
        "mask_size_score_1_5",
        "mask_ground_score_1_5",
        "mask_single_clear_score_1_5",
        "character_present_identity_score_1_5",
        "character_placement_score_1_5",
        "character_grounding_score_1_5",
        "interaction_score_1_5",
        "scene_style_preservation_score_1_5",
        "failure_tags",
        "review_note",
        "auto_mask_diameter_px",
        "auto_mask_center_x",
        "auto_mask_center_y",
    ]
    output = REVIEWS_DIR / "manual-review.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for entry in experiments:
            mask = measurements[entry["id"]]["mask"]
            center = mask.get("center_px", ["", ""])
            row = {
                    "experiment_id": entry["id"],
                    "group_id": entry["group_id"],
                    "element_id": entry["element_id"],
                    "round": entry["round"],
                    "target": entry["target"],
                    "auto_mask_diameter_px": mask.get("equivalent_diameter_px", ""),
                    "auto_mask_center_x": center[0],
                    "auto_mask_center_y": center[1],
                }
            row.update(ratings.get(entry["id"], {}))
            writer.writerow(row)


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    experiments = manifest["experiments"]
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    measurements: dict[str, dict] = {}
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_group: dict[str, list[dict]] = defaultdict(list)

    for entry in experiments:
        scene = load_rgb(ROOT / entry["scene"])
        mask_output = load_rgb(ROOT / entry["mask_output"])
        character_output = load_rgb(ROOT / entry["character_output"])
        mask = find_generated_mask(scene, mask_output)
        component = mask.pop("component", None)
        item = {
            "mask": mask,
            "mask_outside_difference": outside_difference(scene, mask_output, mask),
            "character_outside_former_mask_difference": outside_difference(
                scene, character_output, mask
            ),
        }
        measurements[entry["id"]] = item
        by_pair[(entry["group_id"], entry["element_id"])].append(entry)
        by_group[entry["group_id"]].append(entry)

    pair_stability = {}
    for (group_id, element_id), entries in by_pair.items():
        ordered = sorted(entries, key=lambda item: item["round"])
        first = measurements[ordered[0]["id"]]["mask"]
        second = measurements[ordered[1]["id"]]["mask"]
        if first.get("detected") and second.get("detected"):
            center_distance = math.dist(first["center_px"], second["center_px"])
            diameter_difference = abs(
                first["equivalent_diameter_px"] - second["equivalent_diameter_px"]
            )
            pair_stability[f"{group_id}-{element_id}"] = {
                "round_center_distance_px": round(center_distance, 2),
                "round_diameter_absolute_difference_px": round(diameter_difference, 2),
            }

    diameters = [
        item["mask"]["equivalent_diameter_px"]
        for item in measurements.values()
        if item["mask"].get("detected")
    ]
    summary = {
        "experiment_units": len(experiments),
        "mask_outputs": sum((ROOT / item["mask_output"]).is_file() for item in experiments),
        "character_outputs": sum(
            (ROOT / item["character_output"]).is_file() for item in experiments
        ),
        "detected_masks": len(diameters),
        "target_diameter_px": TARGET_DIAMETER,
        "equivalent_diameter_px": {
            "minimum": round(min(diameters), 2),
            "median": round(float(np.median(diameters)), 2),
            "mean": round(float(np.mean(diameters)), 2),
            "maximum": round(max(diameters), 2),
            "mean_absolute_error_from_target": round(
                float(np.mean(np.abs(np.asarray(diameters) - TARGET_DIAMETER))), 2
            ),
            "within_10px_count": int(
                np.sum(np.abs(np.asarray(diameters) - TARGET_DIAMETER) <= 10)
            ),
        },
    }
    output = {
        "method": {
            "mask_detection": "largest connected component where output is <=15 RGB and differs from source by >20",
            "warning": "Automatic geometry is auxiliary; dark textured scenes require visual review.",
        },
        "summary": summary,
        "pair_stability": pair_stability,
        "experiments": measurements,
    }
    (REVIEWS_DIR / "automatic-measurements.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for group_id, entries in by_group.items():
        build_group_sheet(group_id, sorted(entries, key=lambda x: (x["element_id"], x["round"])), measurements)
    write_manual_review_template(experiments, measurements)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
