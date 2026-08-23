"""Create compact previews for embedding generated scenes in the result workbook."""

from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = ROOT / "style-scene-experiment-20260819"
SOURCE_DIR = EXPERIMENT_DIR / "images"
OUTPUT_DIR = ROOT / ".xlsx-build" / "style-scene-thumbs"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated = sorted(SOURCE_DIR.glob("S*.png"))
    if len(generated) != 42:
        raise SystemExit(f"expected 42 generated scenes, found {len(generated)}")

    for source in generated:
        output = OUTPUT_DIR / f"{source.stem}.jpg"
        with Image.open(source) as image:
            rgb = image.convert("RGB")
            preview = ImageOps.contain(rgb, (640, 357), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (640, 357), "white")
            left = (canvas.width - preview.width) // 2
            top = (canvas.height - preview.height) // 2
            canvas.paste(preview, (left, top))
            canvas.save(output, "JPEG", quality=88, optimize=True)

    print(f"prepared {len(generated)} generated-scene thumbnails")


if __name__ == "__main__":
    main()
