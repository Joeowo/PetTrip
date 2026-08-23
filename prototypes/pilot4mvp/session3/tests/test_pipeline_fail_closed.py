from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from content_service.config import ProviderConfig
from content_service.external_models import ProviderCallError
from content_service.pipeline import run_pipeline
from content_service.snapshot_builder import sha256_file

RESPONSES = ProviderConfig("https://text.test", "responses-test-key", "text-model")
IMAGES = ProviderConfig("https://images.test/v1", "images-test-key", "gpt-image-2")
VALID_WORLD = {
    "theme": "seaside",
    "landmark": "lighthouse",
    "interaction_id": "pet_wave",
    "build_slot_id": "small_shelter",
    "forbidden_objects": ["vehicle"],
    "canvas_width": 512,
    "canvas_height": 288,
    "pixels_per_unit": 16,
}


def _image_base64() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (700, 500), (10, 90, 170)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _responses_success(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "text-request"},
        json={"status": "completed", "output_text": json.dumps(VALID_WORLD)},
    )


def _images_success(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "image-request"},
        json={"created": 1, "data": [{"b64_json": _image_base64()}]},
    )


def test_pipeline_publishes_only_after_all_artifacts_validate(tmp_path) -> None:
    run_dir = tmp_path / "session3-success"
    result = run_pipeline(
        RESPONSES,
        IMAGES,
        allow_chat_compat=True,
        responses_transport=httpx.MockTransport(_responses_success),
        images_transport=httpx.MockTransport(_images_success),
        run_dir=run_dir,
    )
    assert result == run_dir
    fixture_path = run_dir / "test-fixture.json"
    snapshot_path = run_dir / "scene-snapshot.json"
    assert fixture_path.is_file()
    assert not (run_dir / "content-ready.json").exists()
    assert snapshot_path.is_file()
    assert fixture_path.stat().st_mtime_ns >= snapshot_path.stat().st_mtime_ns

    artifact = json.loads((run_dir / "image-artifacts.json").read_text(encoding="utf-8"))[0]
    manifest = json.loads((run_dir / "asset-manifest.json").read_text(encoding="utf-8"))
    background = next(item for item in manifest["assets"] if item["asset_id"] == "beach_background")
    final_path = run_dir / artifact["filename"]
    assert artifact["sha256"] == background["sha256"] == sha256_file(final_path)
    assert (artifact["normalized_width"], artifact["normalized_height"]) == (512, 288)

    evidence_path = run_dir / "external" / "images-call.redacted.json"
    evidence = evidence_path.read_text(encoding="utf-8")
    assert _image_base64() not in evidence
    image_call = json.loads(evidence)
    encoded_metadata = image_call["response"]["data"][0]["b64_json"]
    assert encoded_metadata["redacted"] is True
    assert encoded_metadata["decoded_bytes"] > 0
    assert len(encoded_metadata["raw_sha256"]) == 64
    all_bytes = b"".join(path.read_bytes() for path in run_dir.rglob("*") if path.is_file())
    assert RESPONSES.api_key.encode() not in all_bytes
    assert IMAGES.api_key.encode() not in all_bytes


def test_real_pipeline_requires_explicit_paid_confirmation_before_creating_run(tmp_path) -> None:
    run_dir = tmp_path / "session3-not-confirmed"
    with pytest.raises(ValueError, match="confirm_paid"):
        run_pipeline(
            RESPONSES,
            IMAGES,
            allow_chat_compat=True,
            run_dir=run_dir,
        )
    assert not run_dir.exists()


def test_responses_failure_does_not_call_images_or_publish(tmp_path) -> None:
    image_calls = 0

    def images_handler(request: httpx.Request) -> httpx.Response:
        nonlocal image_calls
        image_calls += 1
        return _images_success(request)

    run_dir = tmp_path / "session3-text-failure"
    with pytest.raises(ProviderCallError):
        run_pipeline(
            RESPONSES,
            IMAGES,
            allow_chat_compat=False,
            responses_transport=httpx.MockTransport(
                lambda request: httpx.Response(401, json={"error": {"message": "invalid API key"}})
            ),
            images_transport=httpx.MockTransport(images_handler),
            run_dir=run_dir,
        )
    assert image_calls == 0
    assert (run_dir / "failure.json").is_file()
    assert not (run_dir / "content-ready.json").exists()
    assert not (run_dir / "test-fixture.json").exists()
    assert not (run_dir / "scene-snapshot.json").exists()


def test_images_failure_does_not_publish_snapshot(tmp_path) -> None:
    run_dir = tmp_path / "session3-image-failure"
    with pytest.raises(ProviderCallError):
        run_pipeline(
            RESPONSES,
            IMAGES,
            allow_chat_compat=True,
            responses_transport=httpx.MockTransport(_responses_success),
            images_transport=httpx.MockTransport(
                lambda request: httpx.Response(400, json={"error": {"message": "content policy refusal"}})
            ),
            run_dir=run_dir,
        )
    assert (run_dir / "failure.json").is_file()
    assert (run_dir / "world-spec.json").is_file()
    assert not (run_dir / "content-ready.json").exists()
    assert not (run_dir / "test-fixture.json").exists()
    assert not (run_dir / "scene-snapshot.json").exists()
