"""会话2 真实 HTTP 自验证。

启动 uvicorn 内容服务，用 HTTP 访问稳定 URI，确认 Snapshot 合法、PNG 可下载且
可被 Pillow 重新打开，并把 HTTP 证据写入该 run 的目录（pilot4mvp/runs/<run_id>/）。

运行: 在 pilot4mvp/session2/ 下执行
    ../../.venv/Scripts/python.exe verify_http.py
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT.parent / "runs"
BASE_URL = "http://127.0.0.1:8000"


def wait_for_health(timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE_URL}/health", timeout=2.0).status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.3)
    raise RuntimeError(f"service did not become healthy: {last_error}")


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "run_server.py")],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_health()
        run_id = httpx.get(f"{BASE_URL}/run-id", timeout=5.0).json()["run_id"]
        snapshot = httpx.get(f"{BASE_URL}/snapshot", timeout=5.0).json()
        manifest = httpx.get(f"{BASE_URL}/manifest", timeout=5.0).json()

        asset_records = []
        for entry in manifest["assets"]:
            asset_id = entry["asset_id"]
            response = httpx.get(f"{BASE_URL}/assets/{asset_id}.png", timeout=10.0)
            assert response.status_code == 200, (asset_id, response.status_code)
            assert response.headers["content-type"] == "image/png"
            image = Image.open(io.BytesIO(response.content))
            image.load()
            asset_records.append(
                {
                    "asset_id": asset_id,
                    "status": response.status_code,
                    "bytes": len(response.content),
                    "sha256": hashlib.sha256(response.content).hexdigest(),
                    "decoded_size": list(image.size),
                }
            )

        negative = httpx.get(f"{BASE_URL}/assets/unknown.png", timeout=5.0)
        assert negative.status_code == 404, (
            "unknown asset must be rejected with 404, got " + str(negative.status_code)
        )

        run_dir = RUNS_DIR / run_id
        report = {
            "run_id": run_id,
            "base_url": BASE_URL,
            "snapshot": {
                "schema_version": snapshot["schema_version"],
                "scene_id": snapshot["scene_id"],
                "layer_count": len(snapshot["layers"]),
                "interaction_count": len(snapshot["interactions"]),
                "build_slot_count": len(snapshot["build_slots"]),
            },
            "assets": asset_records,
            "negative_unknown_asset_status": negative.status_code,
        }
        (run_dir / "http-verification.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))
        print(f"\nVERIFICATION_OK run_dir={run_dir}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
