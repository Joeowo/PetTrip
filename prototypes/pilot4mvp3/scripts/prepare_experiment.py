"""Create the frozen prompt and directory matrix for the mask experiment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GROUPS_PATH = ROOT / "references" / "task-groups.json"
ROLE_REFERENCE = ROOT / "references" / "mutsumi-chibi-reference-neva-v2.png"
RUNS_DIR = ROOT / "runs"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mask_prompt(group: dict, element: dict, other: dict) -> str:
    return f"""Use case: precise-object-edit
Asset type: control-mask image for a fixed 1376x768 game scene
Primary request: edit the supplied scene image by adding exactly one pure black solid circular Mask. Place its center on the safe standing ground at {element['name']}, close enough for a small character to interact with that landmark.
Mask geometry: exactly 108 pixels in diameter on the 1376x768 canvas; perfectly circular; pure black fill; hard clean edge; no outline; no feathering; no transparency; only one circle.
Target: {element['name']}.
Do not target: {other['name']}.
Invariants: change only the pixels occupied by the black circle; keep camera, composition, terrain, landmark shapes, colors, lighting, weather and all pixels outside the circle unchanged.
Constraints: the circle must sit entirely on usable ground; it must not cover the target landmark itself; it must not touch sky or water; no character, no person, no text, no UI, no second circle, no other edits.
"""


def character_prompt(group: dict, element: dict) -> str:
    return f"""Use case: identity-preserve compositing
Asset type: character state image for a fixed 1376x768 game scene
Input images: Image 1 is the scene containing one black circular Mask and is the edit target; Image 2 is the fixed Wakaba Mutsumi Q-version character identity reference.
Primary request: replace only the pure black circular Mask in Image 1 with the single referenced Q-version Wakaba Mutsumi character. The character must be fully contained inside the former circle footprint, including hair, limbs, clothing and feet. Remove the black circle completely.
Character identity: preserve Image 2's cool gray-blue short hair silhouette, teal-green eyes, calm expression, Q-version head/body ratio, outfit shapes and cool gray-blue colors.
Visual style: preserve the scene's Neva-inspired hand-painted 2D storybook language, simplified expressive shapes, muted gouache/pastel blocks and restrained paper-and-brush texture; do not introduce generic anime rendering, photorealism or 3D materials.
Interaction: {element['action']}. This is the only action.
Placement: feet must contact the usable ground at {element['name']}; character scale must fit the 108-pixel Mask diameter; no part may extend beyond the former Mask area.
Invariants: preserve all scene pixels, camera, composition, landmark shape, lighting, weather and NEVA palette outside the former Mask; match the scene's key-light direction and contact shadow.
Constraints: exactly one character; no second action; no duplicate character; no text, UI, logo or watermark; do not move or repaint the landmark; do not leave black pixels.
"""


def main() -> None:
    payload = json.loads(GROUPS_PATH.read_text(encoding="utf-8"))
    if not ROLE_REFERENCE.is_file():
        raise SystemExit(f"missing role reference: {ROLE_REFERENCE}")
    manifest = {
        "experiment_version": payload["experiment_version"],
        "canvas": {"width": 1376, "height": 768},
        "mask": {"shape": "circle", "diameter_px": 108, "fill": "#000000"},
        "role_reference": str(ROLE_REFERENCE.relative_to(ROOT)).replace("\\", "/"),
        "role_reference_sha256": sha256(ROLE_REFERENCE),
        "experiments": [],
    }
    for group in payload["selected_groups"]:
        scene = RUNS_DIR / group["id"] / "scene-neva-v2.png"
        if not scene.is_file():
            raise SystemExit(f"missing scene: {scene}")
        elements = group["elements"]
        for index, element in enumerate(elements):
            other = elements[1 - index]
            for round_number in (1, 2):
                experiment_id = f"{group['id']}-{element['id']}-R{round_number}"
                directory = RUNS_DIR / group["id"] / element["id"] / f"R{round_number}"
                directory.mkdir(parents=True, exist_ok=True)
                mask_path = directory / "prompt-mask.txt"
                character_path = directory / "prompt-character.txt"
                mask_path.write_text(mask_prompt(group, element, other), encoding="utf-8")
                character_path.write_text(character_prompt(group, element), encoding="utf-8")
                manifest["experiments"].append(
                    {
                        "id": experiment_id,
                        "group_id": group["id"],
                        "element_id": element["id"],
                        "round": round_number,
                        "scene": str(scene.relative_to(ROOT)).replace("\\", "/"),
                        "scene_sha256": sha256(scene),
                        "target": element["name"],
                        "risk": element["risk"],
                        "action": element["action"],
                        "mask_prompt": str(mask_path.relative_to(ROOT)).replace("\\", "/"),
                        "character_prompt": str(character_path.relative_to(ROOT)).replace("\\", "/"),
                        "mask_output": str((directory / "mask-output.png").relative_to(ROOT)).replace("\\", "/"),
                        "character_output": str((directory / "character-output.png").relative_to(ROOT)).replace("\\", "/"),
                    }
                )
    output = ROOT / "experiment-manifest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output} with {len(manifest['experiments'])} experiment units")


if __name__ == "__main__":
    main()
