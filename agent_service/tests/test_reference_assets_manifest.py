import hashlib
import json
import mimetypes
from pathlib import Path

from PIL import Image


ASSETS_ROOT = Path(__file__).parents[1] / "data" / "reference_assets"
MANIFEST_PATH = ASSETS_ROOT / "manifest.json"


def test_reference_asset_manifest_matches_bundled_files():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assets = manifest["assets"]

    assert len(assets) == 131
    assert sum(asset["role"] == "style_reference" for asset in assets) == 126

    for asset in assets:
        relative_path = Path(asset["asset_key"])
        assert not relative_path.is_absolute()
        assert ".." not in relative_path.parts

        path = ASSETS_ROOT / relative_path
        assert path.is_file(), asset["asset_key"]

        data = path.read_bytes()
        assert asset["size_bytes"] == len(data)
        assert asset["sha256"] == hashlib.sha256(data).hexdigest()
        assert asset["mime_type"] == mimetypes.guess_type(path.name)[0]

        with Image.open(path) as image:
            assert asset["width"] == image.width
            assert asset["height"] == image.height
