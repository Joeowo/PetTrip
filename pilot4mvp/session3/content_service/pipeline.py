"""会话3 一次性真实外部模型流水线；全部内容校验通过后才标记可服务。"""

from __future__ import annotations

import importlib.metadata
import platform
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ProviderConfig
from .evidence import credential_hits, provider_response_evidence, write_json
from .external_models import CallRecord, ProviderCallError, SCENE_PROMPT, StructuredOutputProvider
from .image_pipeline import ImagesProvider, copy_overlay_assets, decode_and_normalize_image, response_evidence
from .models import ProviderFailure
from .snapshot_builder import build_asset_manifest, build_snapshot, plan_scene, sha256_file, validate_snapshot

SESSION3_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = SESSION3_ROOT.parent / "runs"
OVERLAY_SOURCE = SESSION3_ROOT.parent / "session2" / "assets"


def new_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return RUNS_DIR / f"session3-{stamp}-{secrets.token_hex(2)}"


def _call_evidence(
    call: CallRecord,
    *,
    images: bool = False,
    image_metadata: tuple[int, str] | None = None,
) -> dict[str, Any]:
    if images:
        decoded_bytes, raw_sha256 = image_metadata or (0, "unavailable")
        response = response_evidence(call.response, decoded_bytes, raw_sha256)
    else:
        response = provider_response_evidence(call.response, call.request_id)
    return {
        "endpoint": call.endpoint,
        "method": call.method,
        "http_status": call.http_status,
        "request_id": call.request_id,
        "request": call.request,
        "response": response,
    }


def _write_calls(
    run_dir: Path,
    calls: dict[str, CallRecord],
    api_keys: tuple[str, ...],
    *,
    image_metadata: tuple[int, str] | None = None,
) -> None:
    for name, call in calls.items():
        write_json(
            run_dir / "external" / f"{name}-call.redacted.json",
            _call_evidence(
                call,
                images=name == "images",
                image_metadata=image_metadata if name == "images" else None,
            ),
            api_keys,
        )


def _versions() -> dict[str, Any]:
    packages = ["httpx", "pydantic", "Pillow", "opencv-python", "jsonschema", "python-dotenv"]
    return {
        "python": platform.python_version(),
        "packages": {name: importlib.metadata.version(name) for name in packages},
    }


def _write_versions(path: Path) -> None:
    versions = _versions()
    path.write_text(
        "Python " + versions["python"] + "\n"
        + "\n".join(f"{name}=={version}" for name, version in versions["packages"].items())
        + "\n",
        encoding="utf-8",
    )


def _validate_config(name: str, config: ProviderConfig) -> None:
    if not config.base_url.strip() or not config.api_key.strip() or not config.model.strip():
        raise ValueError(name + " provider config is incomplete")


def run_pipeline(
    responses_config: ProviderConfig,
    images_config: ProviderConfig,
    *,
    allow_chat_compat: bool,
    confirm_paid: bool = False,
    responses_transport=None,
    images_transport=None,
    run_dir: Path | None = None,
    overlay_source: Path = OVERLAY_SOURCE,
) -> Path:
    """执行一次完整内容流水线；Unity 验收前仅生成 content-ready 标记。"""
    _validate_config("Responses", responses_config)
    _validate_config("Images", images_config)
    fixture_mode = responses_transport is not None or images_transport is not None
    if (responses_transport is None) != (images_transport is None):
        raise ValueError("both provider transports must be real or both must be injected fixtures")
    if not fixture_mode and not confirm_paid:
        raise ValueError("real provider calls require confirm_paid=True")

    target = run_dir or new_run_dir()
    target.mkdir(parents=True, exist_ok=False)
    api_keys = (responses_config.api_key, images_config.api_key)
    write_json(
        target / "input.json",
        {
            "run_id": target.name,
            "input": SCENE_PROMPT,
            "responses_model": responses_config.model,
            "images_model": images_config.model,
            "allow_chat_compat": allow_chat_compat,
            "fixture_mode": fixture_mode,
        },
        api_keys,
    )
    _write_versions(target / "versions.txt")

    try:
        structured = StructuredOutputProvider(
            responses_config,
            transport=responses_transport,
            allow_chat_compat=allow_chat_compat,
        ).generate_world_spec(SCENE_PROMPT)
        write_json(target / "world-spec.json", structured.world_spec, api_keys)
        write_json(target / "structured-output-evidence.json", structured.evidence, api_keys)
        _write_calls(target, structured.calls, api_keys)

        plan = plan_scene(structured.world_spec)
        write_json(target / "scene-plan.json", plan, api_keys)

        encoded, image_call = ImagesProvider(
            images_config,
            transport=images_transport,
            timeout=180,
        ).generate()
        raw_path = target / "assets" / "raw" / "beach_background.png"
        normalized_path = target / "assets" / "beach_background.png"
        try:
            artifact = decode_and_normalize_image(
                encoded,
                raw_path,
                normalized_path,
                images_config.model,
                artifact_root=target,
            )
        except ValueError as exc:
            raise ProviderCallError(
                ProviderFailure(
                    stage="image_decode",
                    category="decode",
                    message=str(exc),
                    endpoint=image_call.endpoint,
                    model=images_config.model,
                    http_status=image_call.http_status,
                    request_id=image_call.request_id,
                ),
                calls={"images": image_call},
            ) from exc
        _write_calls(
            target,
            {"images": image_call},
            api_keys,
            image_metadata=(raw_path.stat().st_size, artifact.raw_sha256),
        )
        write_json(target / "image-artifacts.json", [artifact], api_keys)

        copy_overlay_assets(overlay_source, target / "assets")
        manifest = build_asset_manifest(plan, target / "assets")
        background = next(entry for entry in manifest.assets if entry.asset_id == artifact.asset_id)
        if background.sha256 != artifact.sha256 or background.sha256 != sha256_file(normalized_path):
            raise ValueError("ImageArtifact, manifest and normalized file hashes do not match")
        if (background.width, background.height) != (
            artifact.normalized_width,
            artifact.normalized_height,
        ):
            raise ValueError("ImageArtifact and manifest dimensions do not match")
        write_json(target / "asset-manifest.json", manifest, api_keys)

        snapshot = build_snapshot(plan, manifest, structured.world_spec)
        validate_snapshot(snapshot)
        write_json(target / "scene-snapshot.json", snapshot, api_keys)
        write_json(
            target / "validation-report.json",
            {
                "run_id": target.name,
                "content_status": "ready",
                "structured_output_api": structured.evidence.structured_output_api,
                "responses_passed": structured.evidence.responses_passed,
                "images_http_status": image_call.http_status,
                "original_size": [artifact.original_width, artifact.original_height],
                "normalized_size": [artifact.normalized_width, artifact.normalized_height],
                "background_sha256": artifact.sha256,
                "manifest_hash_matches": True,
                "snapshot_schema_valid": True,
                "unity": "not_tested",
            },
            api_keys,
        )

        hits = credential_hits(target, api_keys)
        if hits:
            raise ValueError("credential scan failed for: " + ", ".join(hits))
        marker = "test-fixture.json" if fixture_mode else "content-ready.json"
        write_json(
            target / marker,
            {
                "run_id": target.name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "scene_snapshot": "scene-snapshot.json",
                "asset_manifest": "asset-manifest.json",
                "unity": "not_tested",
            },
            api_keys,
        )
        return target
    except ProviderCallError as exc:
        write_json(target / "failure.json", exc.failure, api_keys)
        _write_calls(target, exc.calls, api_keys)
        raise
    except Exception as exc:
        write_json(
            target / "failure.json",
            {
                "stage": "pipeline",
                "category": "decode",
                "message": str(exc),
                "expected": "validated WorldSpec, PNG, manifest and SceneSnapshot",
            },
            api_keys,
        )
        raise
