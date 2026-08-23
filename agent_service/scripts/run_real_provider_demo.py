"""Drive a real PetTrip service through the HTTP API.

This script never calls Coordinator or providers directly. It records the
Manifest and downloaded SceneArtifact files for manual evidence review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REQUIRED = ("PETTRIP_BASE_URL", "PETTRIP_API_KEY")


def request_json(base_url: str, api_key: str, method: str, path: str, body=None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw or b"{}")
    except (HTTPError, URLError) as exc:
        detail = exc.read().decode("utf-8", "replace") if isinstance(exc, HTTPError) else str(exc)
        raise RuntimeError(f"HTTP {method} {path} failed: {detail}") from exc


def main() -> int:
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        print(f"missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/issue48-real-demo"))
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    base_url = os.environ["PETTRIP_BASE_URL"]
    api_key = os.environ["PETTRIP_API_KEY"]
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {api_key}"}

    _, session = request_json(base_url, api_key, "POST", "/api/v1/sessions")
    session_id = session["session_id"]
    print(f"session_id={session_id}")
    wishes = []
    while True:
        text = input("wish (empty to close): ").strip()
        if not text:
            break
        input_id = f"real-{len(wishes) + 1}"
        _, run = request_json(
            base_url,
            api_key,
            "POST",
            "/api/v1/runs",
            {"session_id": session_id, "command": {"type": "clarification.submit_input", "input_id": input_id, "text": text}},
        )
        print(json.dumps(run, ensure_ascii=False))
        wishes.append(input_id)

    _, closed = request_json(
        base_url,
        api_key,
        "POST",
        "/api/v1/runs",
        {"session_id": session_id, "command": {"type": "clarification.close", "close_request_id": f"close-{session_id}"}},
    )
    destination_id = closed["output"]["structured_data"]["destination_id"]
    print(f"destination_id={destination_id}")
    while True:
        _, manifest = request_json(base_url, api_key, "GET", f"/api/v1/destinations/{destination_id}")
        print(f"phase={manifest['phase']} done={manifest['done']} outcome={manifest['terminal_outcome']}")
        if manifest["done"]:
            break
        time.sleep(args.poll_seconds)

    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for artifact in manifest["scene_artifacts"]:
        _, detail = request_json(base_url, api_key, "GET", f"/api/v1/destinations/{destination_id}/scenes/{artifact['scene_id']}")
        request = Request(f"{base_url.rstrip('/')}{detail['download_url']}", headers=headers)
        with urlopen(request, timeout=30) as response:
            data = response.read()
        actual = hashlib.sha256(data).hexdigest()
        if actual != artifact["render_sha256"]:
            raise RuntimeError(f"artifact SHA mismatch for {artifact['scene_id']}: {actual}")
        target = output_dir / f"{artifact['scene_id']}.png"
        target.write_bytes(data)
        print(f"scene={artifact['scene_id']} path={target} sha256={actual}")
    print(f"evidence_dir={output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
