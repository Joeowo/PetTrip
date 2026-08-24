from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_real_evidence import call


def run(base: str, key: str, out: Path, label: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    evidence = []
    session = call(base, key, "POST", "/api/v1/sessions", None, evidence)
    session_id = session["session_id"]
    wishes = [
        (f"{label}-input-1", "我想带一只温顺的橘猫去海边旅行，希望画面有白色灯塔和温暖夕阳。"),
        (f"{label}-input-2", "这只宠物需要在两个场景中清晰可见：一个沿海滩散步，一个在灯塔旁安静休息。"),
        (f"{label}-input-3", "整体风格希望温馨、自然、适合做可交互的宠物旅行目的地，两个场景保持同一环境和角色设定。"),
    ]
    destination_id = None
    for index, (input_id, text) in enumerate(wishes, 1):
        turn = call(base, key, "POST", "/api/v1/runs", {"session_id": session_id, "command": {"type": "clarification.submit_input", "input_id": input_id, "text": text}}, evidence, idem_key=f"{label}-{input_id}")
        messages = call(base, key, "GET", f"/api/v1/sessions/{session_id}/messages", None, evidence)
        (out / f"messages_after_turn_{index}.json").write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        structured = turn["output"]["structured_data"]
        if structured.get("clarification_closed"):
            destination_id = structured.get("destination_id")
            break
    if destination_id is None:
        raise RuntimeError("clarification did not close from user turns")
    manifest = None
    import time
    for _ in range(180):
        manifest = call(base, key, "GET", f"/api/v1/destinations/{destination_id}", None, evidence)
        if manifest["done"]:
            break
        time.sleep(2)
    if not manifest or not manifest["done"]:
        raise RuntimeError("destination did not finish")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "api_transcript.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"label": label, "session_id": session_id, "destination_id": destination_id, "terminal_outcome": manifest["terminal_outcome"], "publish_eligible": manifest["publish_eligible"], "scene_count": len(manifest.get("scene_artifacts", [])), "api_call_count": len(evidence), "evidence_dir": str(out.resolve())}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    run(os.environ["PETTRIP_BASE_URL"], os.environ["PETTRIP_API_KEY"], args.output_dir, args.label)
