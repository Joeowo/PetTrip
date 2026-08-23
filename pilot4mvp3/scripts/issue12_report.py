"""Render a read-only static evidence page for an Issue 12 run."""

from __future__ import annotations

from collections import Counter
import html
import json
from pathlib import Path
from typing import Any


FORBIDDEN_TEXT = (
    "authorization",
    "api_key",
    "authcode=",
    "signedurl",
    "signed_url",
    "data:",
)
FORBIDDEN_KEYS = ("authorization", "api_key", "authcode", "signedurl", "signed_url")
SAFE_IMAGE_KINDS = {
    "environment",
    "environment_base",
    "locator",
    "semantic_locator",
    "aperture",
    "draw_deterministic_aperture",
    "final",
    "final_pet_scene",
}


def _text(value: Any) -> str:
    raw = "" if value is None else str(value)
    lowered = raw.lower()
    if any(item in lowered for item in FORBIDDEN_TEXT):
        return "[REDACTED]"
    return html.escape(raw)


def _asset(path: Any) -> str:
    if not isinstance(path, str) or not path.strip():
        return ""
    raw = path.strip()
    if ":" in raw or "?" in raw or "#" in raw:
        return ""
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        return ""
    return candidate.as_posix()


def _safe_data(value: Any) -> Any:
    """Remove credential-shaped fields before serializing report metadata."""
    if isinstance(value, dict):
        safe = {}
        has_redaction = False
        for key, item in value.items():
            lowered = str(key).lower()
            if any(forbidden in lowered for forbidden in FORBIDDEN_KEYS):
                has_redaction = True
                continue
            safe[str(key)] = _safe_data(item)
        if has_redaction:
            safe["redacted"] = "[REDACTED]"
        return safe
    if isinstance(value, list):
        return [_safe_data(item) for item in value]
    if isinstance(value, str) and any(
        forbidden in value.lower() for forbidden in FORBIDDEN_TEXT
    ):
        return "[REDACTED]"
    return value


def _json(value: Any) -> str:
    return _text(json.dumps(_safe_data(value), ensure_ascii=False, indent=2))


def _reference_card(reference: dict[str, Any]) -> str:
    path = _asset(reference.get("path", ""))
    image = f'<img src="{html.escape(path)}" alt="reference">' if path else ""
    return (
        '<article class="reference">'
        f"{image}<h4>#{_text(reference.get('order'))} "
        f"{_text(reference.get('role'))}</h4>"
        f"<p>{_text(path)}</p><code>{_text(reference.get('sha256'))}</code>"
        f"<p>{_text(reference.get('width'))} × "
        f"{_text(reference.get('height'))}</p>"
        "</article>"
    )


def _candidate(identifier: str, candidate: dict[str, Any]) -> str:
    snapshot = candidate.get("snapshot") or {}
    prompt = snapshot.get("prompt") or {}
    layers = prompt.get("layers") or {}
    layer_html = "".join(
        f"<dt>{_text(key)}</dt><dd>{_text(value)}</dd>"
        for key, value in layers.items()
    )
    references = "".join(
        _reference_card(item) for item in snapshot.get("references", [])
    )
    request = snapshot.get("request") or {}
    attempts = candidate.get("attempts") or []
    attempts_html = "".join(
        f"<li>attempt {_text(item.get('attempt'))}: task "
        f"{_text(item.get('task_id') or 'not submitted')}; error "
        f"{_text(item.get('error') or 'none')}</li>"
        for item in attempts
    ) or "<li>No remote attempt.</li>"
    review = candidate.get("review") or {}
    return f"""
    <section class="candidate">
      <header><h2>G06 {html.escape(identifier)}</h2><span class="status">{_text(candidate.get('status'))}</span></header>
      <p><b>Snapshot:</b> <code>{_text(snapshot.get('snapshot_sha256'))}</code></p>
      <h3>Five prompt layers</h3><dl>{layer_html}</dl>
      <h3>Rendered prompt</h3><pre>{_text(prompt.get('rendered'))}</pre>
      <h3>Ordered references</h3><div class="references">{references}</div>
      <h3>Redacted request</h3><pre>{_json(request)}</pre>
      <h3>Attempts</h3><ul>{attempts_html}</ul>
      <h3>Human review</h3><p>{_text(review.get('decision') or 'pending')} — {_text(review.get('note') or '')}</p>
    </section>"""


def _document(run_id: Any, body: str) -> str:
    document = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Issue 12 Evidence</title>
<style>
:root {{ color-scheme: light dark; --bg:#f5f1e8; --panel:#fffdf8; --ink:#25231f; --muted:#6d675d; --line:#d6cebf; --accent:#215a54; }}
@media(prefers-color-scheme:dark) {{ :root {{ --bg:#171918;--panel:#222522;--ink:#f3eee5;--muted:#bbb3a5;--line:#454a45;--accent:#82c6bb; }} }}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,sans-serif}} main{{max-width:1120px;margin:auto;padding:2rem 1rem 5rem}} h1{{font-size:clamp(2rem,6vw,4rem);margin:.2em 0}} .notice,.candidate,.assets,.destination,.scene{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:1.2rem;margin:1rem 0}} .notice{{border-left:6px solid var(--accent)}} header{{display:flex;justify-content:space-between;gap:1rem;align-items:center}} .status{{padding:.25rem .7rem;border:1px solid var(--accent);border-radius:999px;color:var(--accent)}} dl{{display:grid;grid-template-columns:minmax(9rem,14rem) 1fr;gap:.5rem 1rem}} dt{{font-weight:700}} dd{{margin:0}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--bg);padding:1rem;border-radius:10px}} code{{overflow-wrap:anywhere}} .references,.images,.counts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}} .reference,.image,.count{{border:1px solid var(--line);border-radius:12px;padding:.8rem;min-width:0}} img{{width:100%;max-height:320px;object-fit:contain;background:white;border-radius:8px}} @media(max-width:600px){{dl{{grid-template-columns:1fr}}}}
</style>
<main>
  <p>Issue 12 · run {_text(run_id)}</p><h1>Experiment evidence</h1>
  <div class="notice"><b>Review boundary</b><p>No automatic quality score or redraw. The planned locator center and deterministic aperture are not final click truth.</p></div>
  {body}
</main>"""
    return "\n".join(line.rstrip() for line in document.splitlines()) + "\n"


def _render_01(manifest: dict[str, Any]) -> str:
    candidates = "".join(
        _candidate(identifier, value)
        for identifier, value in manifest.get("base_candidates", {}).items()
    )
    styles = "".join(
        _reference_card(
            {
                "order": item.get("cell"),
                "role": "style asset",
                "sha256": item.get("output_sha256"),
                **item,
            }
        )
        for item in manifest.get("assets", {}).get("styles", [])
    )
    character = manifest.get("assets", {}).get("character", {})
    split = "".join(
        _reference_card(
            {
                "order": label,
                "role": f"character {label}",
                **character.get(label, {}),
            }
        )
        for label in ("top", "bottom")
    )
    events = "".join(
        f"<li>{_text(item.get('at'))} — {_text(item.get('variant'))}: "
        f"{_text(item.get('event'))}</li>"
        for item in manifest.get("events", [])
    )
    body = (
        '<section class="assets"><h2>Prepared assets</h2>'
        f'<div class="references">{styles}{split}</div></section>{candidates}'
        f'<section class="assets"><h2>State events</h2><ol>{events}</ol></section>'
    )
    return _document(manifest.get("run_id"), body)


def _calls(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    calls = manifest.get("calls", [])
    if isinstance(calls, dict):
        return [
            {"id": identifier, **value}
            for identifier, value in calls.items()
            if isinstance(value, dict)
        ]
    return [item for item in calls if isinstance(item, dict)]


def _call_images(call: dict[str, Any]) -> str:
    kind = str(call.get("phase") or call.get("kind") or call.get("step") or call.get("type") or "")
    if kind not in SAFE_IMAGE_KINDS:
        return ""
    result = call.get("result") or {}
    outputs = result.get("outputs") or call.get("outputs") or call.get("artifacts") or []
    if isinstance(outputs, dict):
        outputs = [outputs]
    images = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        path = _asset(output.get("path"))
        if not path:
            continue
        images.append(
            '<article class="image">'
            f'<img src="{html.escape(path)}" alt="{_text(kind)}">'
            f"<h4>{_text(kind)}</h4><p>{_text(path)}</p>"
            f"<code>{_text(output.get('sha256'))}</code></article>"
        )
    return "".join(images)


def _artifact_image(artifact: dict[str, Any], label: str) -> str:
    path = _asset(artifact.get("path"))
    if not path:
        return ""
    return (
        '<article class="image">'
        f'<img src="{html.escape(path)}" alt="{_text(label)}">'
        f"<h4>{_text(label)}</h4><p>{_text(path)}</p>"
        f"<code>{_text(artifact.get('sha256'))}</code></article>"
    )


def _call_card(call: dict[str, Any], artifacts: dict[str, Any] | None = None) -> str:
    result = call.get("result") or {}
    attempt = call.get("attempt") or {}
    metadata = result.get("task") or call.get("task_safe_metadata") or call.get("safe_metadata")
    details = []
    for title, value in (
        ("Measurement", result.get("measurement") or call.get("measurement")),
        ("Task safe metadata", metadata),
        ("Error", attempt.get("error") or result.get("technical_error") or call.get("error")),
        ("Blocked", call.get("blocked_by") or call.get("blocked")),
    ):
        if value not in (None, "", [], {}):
            details.append(f"<h4>{title}</h4><pre>{_json(value)}</pre>")
    derived = ""
    if call.get("phase") == "locator" and artifacts:
        aperture_id = (
            f"aperture:{call.get('destination')}:{call.get('route')}:{call.get('scene')}"
        )
        aperture = artifacts.get(aperture_id)
        if isinstance(aperture, dict):
            derived = _artifact_image(aperture, "deterministic aperture")
    return (
        '<article class="candidate">'
        f"<header><h3>{_text(call.get('id') or call.get('kind') or call.get('step'))}</h3>"
        f'<span class="status">{_text(call.get("status"))}</span></header>'
        f'<div class="images">{_call_images(call)}{derived}</div>{"".join(details)}</article>'
    )


def _provenance(manifest: dict[str, Any]) -> Any:
    imported = {
        identifier: artifact.get("origin")
        for identifier, artifact in (manifest.get("artifacts") or {}).items()
        if identifier.startswith("environment:G06:") and isinstance(artifact, dict) and artifact.get("origin")
    }
    if imported:
        return imported
    destinations = manifest.get("destinations") or manifest.get("workflows") or {}
    if isinstance(destinations, dict):
        g06 = destinations.get("G06") or {}
        if isinstance(g06, dict) and g06.get("provenance"):
            return g06["provenance"]
    imports = manifest.get("imports") or {}
    if isinstance(imports, dict):
        return imports.get("G06")
    return None


def _value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _recovery_measurement(measurement: dict[str, Any], identifier: Any) -> str:
    selected = measurement.get("selected_candidate") or {}
    ellipse = selected.get("ellipse") or {}
    colors = {
        key: selected.get(key)
        for key in (
            "max_channel_p90",
            "chroma_p90",
            "fraction_max_channel_le_20",
            "delta_luminance_mean",
        )
        if key in selected
    }
    shape = {
        key: selected.get(key)
        for key in ("bbox", "bbox_width", "bbox_height", "aspect_ratio", "fill_ratio")
        if key in selected
    }
    if ellipse:
        shape["ellipse"] = ellipse
    aperture = measurement.get("aperture") or measurement.get("aperture_artifact")
    return (
        '<article class="image"><h4>Measurement '
        f"{_text(identifier)}</h4>"
        f"<p><b>Color diagnostics</b></p><pre>{_json(colors)}</pre>"
        f"<p><b>Shape diagnostics</b></p><pre>{_json(shape)}</pre>"
        f"<p><b>Selection score (not quality)</b>: "
        f"{_text(selected.get('score'))}</p>"
        f"<p><b>Selection</b>: {_text(selected.get('accepted'))}</p>"
        f"<p><b>Aperture</b></p><pre>{_json(aperture)}</pre>"
        f"<pre>{_json(measurement.get('rejection_counts') or {})}</pre></article>"
    )


def _recovery_calls(title: str, value: Any, expected: str) -> str:
    if isinstance(value, dict):
        calls = value.get("calls") or value.get("call_ids") or []
        results = value.get("results") or value.get("outputs") or []
    elif isinstance(value, list):
        calls, results = value, []
    else:
        calls, results = [], []
    return (
        f'<section class="assets"><h2>{_text(title)} '
        f"({len(calls)} calls / {expected})</h2>"
        f"<pre>{_json({'calls': calls, 'results': results})}</pre></section>"
    )


def _render_recovery(manifest: dict[str, Any]) -> str:
    recovery = manifest.get("recovery") or manifest
    if not isinstance(recovery, dict):
        recovery = {}
    artifacts = recovery.get("artifacts") or {}
    calls = _calls(recovery)
    source = _value(
        recovery,
        "source_manifest_sha256",
        "source_manifest_hash",
    )
    source_manifest = recovery.get("source_manifest")
    if isinstance(source_manifest, dict):
        source = source or _value(source_manifest, "sha256", "hash")
    plan = recovery.get("plan") or manifest.get("plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    approval = _value(recovery, "plan_approval", "approval") or plan.get("approval")
    baseline = recovery.get("baseline") or recovery.get("old") or {}
    refreshed = recovery.get("new") or recovery.get("recovered") or {}
    negative = _value(recovery, "negative_rejected", "negative_rejections")
    measurements = recovery.get("measurements") or manifest.get("measurements") or []
    if not measurements:
        measurements = [
            {"id": identifier, **artifact["recovery_detection"]}
            for identifier, artifact in artifacts.items()
            if isinstance(artifact, dict) and "recovery_detection" in artifact
        ]
    if isinstance(measurements, dict):
        measurements = [{"id": key, **value} for key, value in measurements.items() if isinstance(value, dict)]
    measurement_html = "".join(
        _recovery_measurement(item, item.get("id", index + 1))
        for index, item in enumerate(measurements)
        if isinstance(item, dict)
    )
    provenance = recovery.get("provenance") or manifest.get("provenance") or recovery.get("source")
    preserved = [
        {"id": identifier, **artifact}
        for identifier, artifact in artifacts.items()
        if identifier.startswith("final:")
    ]
    new_finals = [call for call in calls if call.get("phase") == "final"]
    old_failed = 16 - len([item for item in measurements if item.get("selected_candidate")])
    final_review = recovery.get("final_review") or manifest.get("final_review")
    review_html = (
        f'<section class="assets"><h2>Final review</h2>'
        f"<pre>{_json(final_review)}</pre></section>"
        if final_review is not None
        else ""
    )
    body = f"""
    <section class="assets"><h2>Recovery source and approval</h2>
      <dl><dt>Source manifest hash</dt><dd><code>{_text(source)}</code></dd>
      <dt>Plan approval</dt><dd><pre>{_json(approval)}</pre></dd></dl>
    </section>
    <section class="assets"><h2>Recovery counts</h2>
      <dl><dt>Old selected</dt><dd>{16 - old_failed} / 16</dd><dt>New selected</dt><dd>{len(measurements)} / 16</dd>
      <dt>Preserved final</dt><dd>{len(preserved)}</dd><dt>New final calls</dt><dd>{len(new_finals)}</dd>
      <dt>Negative rejected</dt><dd><pre>{_json(negative)}</pre></dd></dl>
    </section>
    <section class="destination"><h2>{len(measurements)} measurements</h2>
      <p>Score is used only to select a marker candidate; it is not a quality score.</p>
      <div class="images">{measurement_html}</div>
    </section>
    {_recovery_calls("Preserved final", recovery.get("preserved_final") or recovery.get("preserved"), "6")}
    {_recovery_calls("New final", recovery.get("new_final") or recovery.get("new_finals"), "10")}
    <section class="assets"><h2>Provenance</h2><pre>{_json(provenance)}</pre></section>
    {review_html}
    """
    return _document(manifest.get("run_id"), body)


def _render_02(manifest: dict[str, Any]) -> str:
    plan = manifest.get("plan") or {}
    plan_hash = manifest.get("plan_hash")
    approval = manifest.get("plan_approval")
    if isinstance(plan, dict):
        plan_hash = plan_hash or plan.get("plan_sha256") or plan.get("sha256") or plan.get("hash")
        approval = approval or plan.get("approval")

    calls = _calls(manifest)
    counts = Counter(str(call.get("status") or "unknown") for call in calls)
    count_html = "".join(
        f'<div class="count"><b>{_text(status)}: {count}</b></div>'
        for status, count in sorted(counts.items())
    )

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for call in calls:
        key = (
            str(call.get("destination") or "unassigned"),
            str(call.get("route") or "default"),
            str(call.get("scene") or "unassigned"),
        )
        grouped.setdefault(key, []).append(call)
    artifacts = manifest.get("artifacts") or {}
    groups = "".join(
        '<section class="scene">'
        f"<h3>{_text(destination)} · {_text(route)} · scene {_text(scene)}</h3>"
        f"{''.join(_call_card(call, artifacts) for call in group_calls)}</section>"
        for (destination, route, scene), group_calls in grouped.items()
    )

    provenance = _provenance(manifest)
    provenance_html = (
        f'<section class="assets"><h2>G06 imported provenance</h2>'
        f"<pre>{_json(provenance)}</pre></section>"
        if provenance
        else ""
    )
    final_review = manifest.get("final_review")
    review_html = (
        f'<section class="assets"><h2>Final review</h2>'
        f"<pre>{_json(final_review)}</pre></section>"
        if final_review is not None
        else ""
    )
    body = f"""
    <section class="assets"><h2>Approved plan</h2>
      <dl><dt>Plan hash</dt><dd><code>{_text(plan_hash)}</code></dd>
      <dt>Approval</dt><dd><pre>{_json(approval)}</pre></dd></dl>
    </section>
    <section class="assets"><h2>{len(calls)} calls</h2><div class="counts">{count_html}</div></section>
    <section class="destination"><h2>Evidence by destination / route / scene</h2>{groups}</section>
    {provenance_html}{review_html}
    """
    return _document(manifest.get("run_id"), body)


def render_evidence(manifest_path: Path, output_path: Path) -> None:
    before = manifest_path.read_bytes()
    manifest = json.loads(before.decode("utf-8"))
    schema_version = manifest.get("schema_version")
    if schema_version == "issue12-recovery/0.1":
        document = _render_recovery(manifest)
    elif schema_version == "issue12-run/0.2":
        document = _render_02(manifest)
    else:
        document = _render_01(manifest)
    lowered = document.lower()
    if any(item in lowered for item in FORBIDDEN_TEXT):
        raise ValueError("evidence page contains forbidden credential material")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    if manifest_path.read_bytes() != before:
        raise RuntimeError("report rendering modified the manifest")
