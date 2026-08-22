"""Render a read-only static evidence page for an Issue 12 run."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


FORBIDDEN = ("authorization", "api_key", "data:", "authcode=")


def _text(value: Any) -> str:
    raw = "" if value is None else str(value)
    lowered = raw.lower()
    if any(item in lowered for item in FORBIDDEN):
        return "[REDACTED]"
    return html.escape(raw)


def _asset(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return ""
    return candidate.as_posix()


def _reference_card(reference: dict[str, Any]) -> str:
    path = _asset(reference.get("path", ""))
    image = f'<img src="{html.escape(path)}" alt="reference">' if path else ""
    return (
        '<article class="reference">'
        f"{image}<h4>#{_text(reference.get('order'))} {_text(reference.get('role'))}</h4>"
        f"<p>{_text(path)}</p><code>{_text(reference.get('sha256'))}</code>"
        f"<p>{_text(reference.get('width'))} × {_text(reference.get('height'))}</p>"
        "</article>"
    )


def _candidate(identifier: str, candidate: dict[str, Any]) -> str:
    snapshot = candidate.get("snapshot") or {}
    prompt = snapshot.get("prompt") or {}
    layers = prompt.get("layers") or {}
    layer_html = "".join(
        f"<dt>{_text(key)}</dt><dd>{_text(value)}</dd>" for key, value in layers.items()
    )
    references = "".join(_reference_card(item) for item in snapshot.get("references", []))
    request = snapshot.get("request") or {}
    attempts = candidate.get("attempts") or []
    attempts_html = "".join(
        f"<li>attempt {_text(item.get('attempt'))}: task {_text(item.get('task_id') or 'not submitted')}"
        f"; error {_text(item.get('error') or 'none')}</li>" for item in attempts
    ) or "<li>No remote attempt.</li>"
    review = candidate.get("review") or {}
    return f"""
    <section class="candidate">
      <header><h2>G06 {html.escape(identifier)}</h2><span class="status">{_text(candidate.get('status'))}</span></header>
      <p><b>Snapshot:</b> <code>{_text(snapshot.get('snapshot_sha256'))}</code></p>
      <h3>Five prompt layers</h3><dl>{layer_html}</dl>
      <h3>Rendered prompt</h3><pre>{_text(prompt.get('rendered'))}</pre>
      <h3>Ordered references</h3><div class="references">{references}</div>
      <h3>Redacted request</h3><pre>{_text(json.dumps(request, ensure_ascii=False, indent=2))}</pre>
      <p><b>Idempotency key:</b> {_text(snapshot.get('idem_key'))}</p>
      <h3>Attempts</h3><ul>{attempts_html}</ul>
      <h3>Human review</h3><p>{_text(review.get('decision') or 'pending')} — {_text(review.get('note') or '')}</p>
    </section>"""


def render_evidence(manifest_path: Path, output_path: Path) -> None:
    before = manifest_path.read_bytes()
    manifest = json.loads(before.decode("utf-8"))
    candidates = "".join(
        _candidate(identifier, value)
        for identifier, value in manifest.get("base_candidates", {}).items()
    )
    styles = "".join(
        _reference_card({"order": item.get("cell"), "role": "style asset", "sha256": item.get("output_sha256"), **item})
        for item in manifest.get("assets", {}).get("styles", [])
    )
    character = manifest.get("assets", {}).get("character", {})
    split = "".join(
        _reference_card({"order": label, "role": f"character {label}", **character.get(label, {})})
        for label in ("top", "bottom")
    )
    events = "".join(
        f"<li>{_text(item.get('at'))} — {_text(item.get('variant'))}: {_text(item.get('event'))}</li>"
        for item in manifest.get("events", [])
    )
    document = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Issue 12 Evidence</title>
<style>
:root {{ color-scheme: light dark; --bg:#f5f1e8; --panel:#fffdf8; --ink:#25231f; --muted:#6d675d; --line:#d6cebf; --accent:#215a54; }}
@media(prefers-color-scheme:dark) {{ :root {{ --bg:#171918;--panel:#222522;--ink:#f3eee5;--muted:#bbb3a5;--line:#454a45;--accent:#82c6bb; }} }}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,sans-serif}} main{{max-width:1120px;margin:auto;padding:2rem 1rem 5rem}} h1{{font-size:clamp(2rem,6vw,4rem);margin:.2em 0}} .notice,.candidate,.assets{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:1.2rem;margin:1rem 0}} .notice{{border-left:6px solid var(--accent)}} header{{display:flex;justify-content:space-between;gap:1rem;align-items:center}} .status{{padding:.25rem .7rem;border:1px solid var(--accent);border-radius:999px;color:var(--accent)}} dl{{display:grid;grid-template-columns:minmax(9rem,14rem) 1fr;gap:.5rem 1rem}} dt{{font-weight:700}} dd{{margin:0}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--bg);padding:1rem;border-radius:10px}} code{{overflow-wrap:anywhere}} .references{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}} .reference{{border:1px solid var(--line);border-radius:12px;padding:.8rem;min-width:0}} img{{width:100%;max-height:240px;object-fit:contain;background:white;border-radius:8px}} @media(max-width:600px){{dl{{grid-template-columns:1fr}}}}
</style>
<main>
  <p>Issue 12 · run {_text(manifest.get('run_id'))}</p><h1>Experiment evidence</h1>
  <div class="notice"><b>Review boundary</b><p>No automatic quality score or redraw. The planned locator center and deterministic aperture are not final click truth.</p></div>
  <section class="assets"><h2>Prepared assets</h2><div class="references">{styles}{split}</div></section>
  {candidates}
  <section class="assets"><h2>State events</h2><ol>{events}</ol></section>
</main>"""
    document = "\n".join(line.rstrip() for line in document.splitlines()) + "\n"
    lowered = document.lower()
    if "authorization" in lowered or "api_key" in lowered or "data:" in lowered or "authcode=" in lowered:
        raise ValueError("evidence page contains forbidden credential material")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    if manifest_path.read_bytes() != before:
        raise RuntimeError("report rendering modified the manifest")
