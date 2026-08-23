"""Create compact JPEG previews for the experiment workbook."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / ".xlsx-build"
THUMBS_DIR = BUILD_DIR / "thumbs"


def thumbnail_path(source: Path) -> Path:
    key = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:16]
    return THUMBS_DIR / f"{key}.jpg"


def create_thumbnail(source: Path) -> Path:
    output = thumbnail_path(source)
    if output.is_file() and output.stat().st_mtime >= source.stat().st_mtime:
        return output
    with Image.open(source) as image:
        rgb = image.convert("RGB")
        preview = ImageOps.contain(rgb, (480, 270), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (480, 270), (245, 246, 243))
        x = (canvas.width - preview.width) // 2
        y = (canvas.height - preview.height) // 2
        canvas.paste(preview, (x, y))
        canvas.save(output, "JPEG", quality=82, optimize=True)
    return output


def main() -> None:
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT / "references" / "reference-catalog.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "experiment-manifest.json").read_text(encoding="utf-8"))

    sources: set[Path] = set()
    for item in catalog:
        sources.add(ROOT / item["local_path"])
    for entry in manifest["experiments"]:
        sources.add(ROOT / entry["scene"])
        sources.add(ROOT / entry["mask_output"])
        sources.add(ROOT / entry["character_output"])
    sources.update(
        {
            ROOT / "references" / "mutsumi-chibi-reference-neva-v2.png",
            ROOT / "references" / "neva-official" / "neva-steam-06.jpg",
            ROOT / "references" / "neva-official" / "neva-steam-14.jpg",
        }
    )

    assets = {}
    for source in sorted(sources):
        if not source.is_file():
            raise SystemExit(f"missing workbook image: {source}")
        relative = source.relative_to(ROOT).as_posix()
        assets[relative] = thumbnail_path(source).relative_to(ROOT).as_posix()
        create_thumbnail(source)
    (BUILD_DIR / "workbook-assets.json").write_text(
        json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"prepared {len(assets)} workbook thumbnails")


if __name__ == "__main__":
    main()
