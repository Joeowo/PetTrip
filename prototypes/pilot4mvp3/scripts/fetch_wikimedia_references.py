"""Fetch the frozen Wikimedia Commons landscape candidate set."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "references" / "candidates"
CATALOG_PATH = ROOT / "references" / "reference-catalog.json"
API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "PetTrip-mask-validation/0.1 (research fixture)"

CANDIDATE_TITLES = [
    "File:Azerbajiani landscape - Another version.jpg",
    "File:Hendrik Voogd - Italian landscape with Umbrella Pines.jpg",
    "File:Icelandic Landscape near Neskaupstaður July 2014.JPG",
    "File:Landscape Arnisee-region.JPG",
    "File:Tuscan landscape with lonely tree.jpg",
    "File:Gjipe beach, Albania.JPG",
    "File:Nørre Vorupør Coast one third sky 2012-11-18.jpg",
    "File:Princetown (AU), Port Campbell National Park, Twelve Apostles -- 2019 -- 0969.jpg",
    "File:Blue Mountains National Park (AU), Three Sisters -- 2019 -- 1987-9.jpg",
    "File:The PEFO Tepees.jpg",
    "File:Dunes, Désert du Thar.jpg",
    "File:Puesta de sol, desierto de Namib, Namibia, 2018-08-05, DD 84-90 PAN.jpg",
    "File:Salar de Tara, Chile, 2016-02-07, DD 64-67 PAN.jpg",
    "File:Beech Forest (AU), Great Otway National Park, Beauchamp Falls -- 2019 -- 1271.jpg",
    "File:Forest road Slavne 2017 BW G9.jpg",
    "File:Forested hills in Lysekil in fog - B&W.jpg",
    "File:Wooden staircase steps in the forest of Hallasan Park Eorimok Trail at dusk on Jeju Island in South Korea.jpg",
    "File:Blue Lake in Mount Cook National Park.jpg",
    "File:Lake Tekapo 01.jpg",
    "File:Nigeen Lake pano (edited).jpg",
]


def fetch_json(params: dict[str, str]) -> dict:
    query = "&".join(f"{quote(k)}={quote(v)}" for k, v in params.items())
    request = Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
    for attempt in range(5):
        try:
            with urlopen(request, timeout=40) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code != 429 or attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def download_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(5):
        try:
            with urlopen(request, timeout=90) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code != 429 or attempt == 4:
                raise
            time.sleep(8 * (attempt + 1))
    raise RuntimeError("unreachable")


def safe_name(title: str) -> str:
    value = title.removeprefix("File:")
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return value[:100] + ".jpg"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog: list[dict] = []
    for index, title in enumerate(CANDIDATE_TITLES, start=1):
        payload = fetch_json(
            {
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|size|mime",
                "iiurlwidth": "960",
                "format": "json",
            }
        )
        pages = payload.get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        info = (page.get("imageinfo") or [{}])[0]
        thumb_url = info.get("thumburl") or info.get("url")
        if not thumb_url:
            raise RuntimeError(f"No image URL for {title}")
        filename = f"{index:02d}_{safe_name(title)}"
        local_path = OUT_DIR / filename
        local_path.write_bytes(download_bytes(thumb_url))
        metadata = info.get("extmetadata") or {}
        catalog.append(
            {
                "id": f"C{index:02d}",
                "title": page.get("title", title),
                "file_page_url": "https://commons.wikimedia.org/wiki/" + quote(page.get("title", title).replace(" ", "_")),
                "original_url": info.get("url"),
                "thumbnail_url": thumb_url,
                "local_path": str(local_path.relative_to(ROOT)).replace("\\", "/"),
                "width": info.get("width"),
                "height": info.get("height"),
                "mime": info.get("mime"),
                "author": (metadata.get("Artist") or {}).get("value"),
                "license": (metadata.get("LicenseShortName") or {}).get("value"),
                "description": (metadata.get("ImageDescription") or {}).get("value"),
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        print(f"[{index:02d}/20] {title} -> {local_path.name}", flush=True)
        time.sleep(2)
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {CATALOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
