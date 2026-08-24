"""Run and capture a real-provider PetTrip HTTP happy path."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def call(base: str, key: str, method: str, path: str, body, evidence: list[dict], idem_key: str | None = None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    req = Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(req, timeout=180) as response:
            raw = response.read()
            status = response.status
    except (HTTPError, URLError) as exc:
        raw = exc.read() if isinstance(exc, HTTPError) else str(exc).encode()
        status = exc.code if isinstance(exc, HTTPError) else 0
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        payload = {"raw": raw.decode("utf-8", "replace")}
    evidence.append({"method": method, "path": path, "request": body, "status": status, "response": payload})
    if status < 200 or status >= 300:
        raise RuntimeError(f"{method} {path} -> HTTP {status}: {payload}")
    return payload


def run(base: str, key: str, out: Path, label: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    evidence: list[dict] = []
    session = call(base, key, "POST", "/api/v1/sessions", None, evidence)
    session_id = session["session_id"]
    wishes = [
        (f"{label}-input-1", "我想带一只温顺的橘猫去海边旅行，希望画面有灯塔和温暖的夕阳。"),
        (f"{label}-input-2", "猫需要在两个场景里都清晰可见：一个是海边散步，一个是在灯塔旁休息。"),
        (f"{label}-input-3", "整体风格希望温馨、自然、适合做可交互的宠物旅行目的地。"),
    ]
    destination_id = None
    for index, (input_id, text) in enumerate(wishes, start=1):
        turn = call(base, key, "POST", "/api/v1/runs", {
            "session_id": session_id,
            "command": {"type": "clarification.submit_input", "input_id": input_id, "text": text},
        }, evidence, idem_key=f"{label}-{input_id}")
        messages = call(
            base,
            key,
            "GET",
            f"/api/v1/sessions/{session_id}/messages",
            None,
            evidence,
        )
        (out / f"messages_after_turn_{index}.json").write_text(
            json.dumps(messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        turn_output = turn["output"]["structured_data"]
        if turn_output.get("clarification_closed"):
            destination_id = turn_output.get("destination_id")
            break
    if destination_id is None:
        raise RuntimeError("clarification did not close from user turns")
    manifest = None
    for _ in range(180):
        manifest = call(base, key, "GET", f"/api/v1/destinations/{destination_id}", None, evidence)
        if manifest["done"]:
            break
        time.sleep(2)
    if not manifest or not manifest["done"]:
        raise RuntimeError("destination did not finish")
    (out / "api_transcript.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for index, artifact in enumerate(manifest.get("scene_artifacts", [])):
        detail = call(base, key, "GET", f"/api/v1/destinations/{destination_id}/scenes/{artifact['scene_id']}", None, evidence)
        (out / f"scene_{index+1}_detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        req = Request(base.rstrip("/") + detail["download_url"], headers={"Authorization": f"Bearer {key}"})
        with urlopen(req, timeout=180) as response:
            image = response.read()
        sha = hashlib.sha256(image).hexdigest()
        if sha != artifact["render_sha256"]:
            raise RuntimeError(f"sha mismatch for {artifact['scene_id']}: {sha}")
        (out / f"scene_{index+1}_{artifact['scene_id']}.png").write_bytes(image)
    (out / "api_transcript.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "label": label,
        "session_id": session_id,
        "destination_id": destination_id,
        "terminal_outcome": manifest["terminal_outcome"],
        "publish_eligible": manifest["publish_eligible"],
        "scene_count": len(manifest.get("scene_artifacts", [])),
        "api_call_count": len(evidence),
        "evidence_dir": str(out.resolve()),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    run(os.environ["PETTRIP_BASE_URL"], os.environ["PETTRIP_API_KEY"], args.output_dir, args.label)
