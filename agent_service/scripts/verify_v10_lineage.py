from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "real-provider-semantic-v10"
DB = OUT / "runtime" / "data" / "agent.db"
PET_SHA = "fa4ad2248ff23300ca21ce833bb6cdef9ec49a56308d50f8624b4558b622b7c3"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask_outside_diff(aperture_path: Path, final_path: Path, mask_path: Path) -> dict:
    aperture = Image.open(aperture_path).convert("RGBA")
    final = Image.open(final_path).convert("RGBA")
    mask = Image.open(mask_path).convert("L")
    assert aperture.size == final.size == mask.size
    changed = 0
    outside = 0
    max_delta = 0
    for y in range(mask.height):
        for x in range(mask.width):
            if mask.getpixel((x, y)) == 0:
                outside += 1
                a = aperture.getpixel((x, y))
                b = final.getpixel((x, y))
                delta = max(abs(a[i] - b[i]) for i in range(4))
                max_delta = max(max_delta, delta)
                if a != b:
                    changed += 1
    return {
        "aperture_sha256": sha(aperture_path.read_bytes()),
        "final_sha256": sha(final_path.read_bytes()),
        "mask_sha256": sha(mask_path.read_bytes()),
        "outside_pixels": outside,
        "changed_pixels_outside_mask": changed,
        "max_channel_delta_outside_mask": max_delta,
        "outside_pixel_lineage_valid": changed == 0,
    }


def rows(con: sqlite3.Connection, sql: str):
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(sql)]


def main() -> None:
    con = sqlite3.connect(DB)
    artifacts = rows(con, "select * from scene_artifacts order by created_at")
    plans = rows(con, "select * from scene_plans order by order_index")
    snapshots = rows(con, "select * from prompt_snapshots where operation_type='scene_render' order by created_at")
    checks = []
    for index, artifact in enumerate(artifacts, start=1):
        scene_id = artifact["scene_id"]
        files = rows(con, "select * from files where id like ? order by created_at", (f"scene_{scene_id[6:]}%",))
        final = next((Path(OUT / "runtime" / "data" / row["rel_path"]) for row in files), None)
        plan = next(row for row in plans if row["scene_id"] == scene_id)
        snapshot = next(row for row in snapshots if row["snapshot_id"] == artifact["prompt_snapshot_id"])
        params = json.loads(snapshot["model_params"])
        aperture = OUT / "runtime" / "data" / "files" / "generated" / f"aperture_{scene_id.replace('scene_', '')}.png"
        mask = OUT / "runtime" / "data" / "files" / "generated" / f"mask_{scene_id.replace('scene_', '')}.png"
        if final is None or not aperture.is_file() or not mask.is_file():
            raise RuntimeError(f"missing lineage files for {scene_id}")
        diff = mask_outside_diff(aperture, final, mask)
        checks.append({
            "scene_id": scene_id,
            "order_index": plan["order_index"],
            "state_label": plan["state_label"],
            "pet_behavior": plan["pet_behavior"],
            "pet_emotion": plan["pet_emotion"],
            "prompt_snapshot_id": snapshot["snapshot_id"],
            "prompt_contains_behavior": plan["pet_behavior"] in snapshot["prompt_text"],
            "prompt_contains_emotion": plan["pet_emotion"] in snapshot["prompt_text"],
            "prompt_contains_canonical_reference": "canonical fixed pet identity reference" in snapshot["prompt_text"],
            "pet_reference_sha256": params.get("pet_reference_sha256"),
            "pet_reference_sha_matches": params.get("pet_reference_sha256") == PET_SHA,
            **diff,
        })
    result = {
        "destination_id": artifacts[0]["destination_id"],
        "scene_count": len(checks),
        "shared_environment_sha256": artifacts[0]["shared_environment_sha256"],
        "shared_environment_same_for_all": len({row["shared_environment_sha256"] for row in artifacts}) == 1,
        "canonical_pet_sha256": PET_SHA,
        "scenes": checks,
        "all_scene_plan_prompts_verified": all(c["prompt_contains_behavior"] and c["prompt_contains_emotion"] and c["prompt_contains_canonical_reference"] for c in checks),
        "all_pet_reference_sha_verified": all(c["pet_reference_sha_matches"] for c in checks),
        "all_mask_outside_lineage_verified": all(c["outside_pixel_lineage_valid"] for c in checks),
    }
    (OUT / "v10_lineage_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
