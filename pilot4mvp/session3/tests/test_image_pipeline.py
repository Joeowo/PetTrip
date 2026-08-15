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
from content_service.image_pipeline import (
    ImagesProvider,
    copy_overlay_assets,
    decode_and_normalize_image,
    response_evidence,
)

CONFIG = ProviderConfig("https://images.test/v1", "test-key", "gpt-image-2")
SESSION2_ASSETS = Path(__file__).resolve().parents[2] / "session2" / "assets"


def _png_base64(size=(1402, 1122), color=(12, 80, 160)) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_images_provider_calls_generations_with_long_timeout() -> None:
    encoded = _png_base64((64, 64))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/images/generations"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload == {
            "model": "gpt-image-2",
            "prompt": payload["prompt"],
            "n": 1,
        }
        return httpx.Response(200, headers={"x-request-id": "image-request"}, json={"data": [{"b64_json": encoded}]})

    provider = ImagesProvider(CONFIG, transport=httpx.MockTransport(handler), timeout=180)
    actual, call = provider.generate()
    assert actual == encoded
    assert call.http_status == 200
    assert call.request_id == "image-request"
    assert provider.timeout >= 120


@pytest.mark.parametrize("data", [{}, {"data": []}, {"data": [{"b64_json": "a"}, {"b64_json": "b"}]}])
def test_images_provider_rejects_missing_or_multiple_results(data) -> None:
    provider = ImagesProvider(
        CONFIG,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)),
    )
    with pytest.raises(ProviderCallError) as caught:
        provider.generate()
    assert caught.value.failure.category == "decode"
    assert "images" in caught.value.calls


def test_decode_and_normalize_records_real_dimensions_and_hashes(tmp_path) -> None:
    raw_path = tmp_path / "assets" / "raw" / "beach_background.png"
    normalized_path = tmp_path / "assets" / "beach_background.png"
    artifact = decode_and_normalize_image(
        _png_base64(), raw_path, normalized_path, "gpt-image-2", artifact_root=tmp_path
    )

    assert (artifact.original_width, artifact.original_height) == (1402, 1122)
    assert (artifact.normalized_width, artifact.normalized_height) == (512, 288)
    assert artifact.raw_sha256 != artifact.sha256
    with Image.open(raw_path) as raw:
        assert raw.size == (1402, 1122)
    with Image.open(normalized_path) as normalized:
        assert normalized.size == (512, 288)
        assert normalized.format == "PNG"


@pytest.mark.parametrize("encoded", ["not-base64!", base64.b64encode(b"not an image").decode()])
def test_decode_and_normalize_rejects_invalid_image_data(tmp_path, encoded) -> None:
    with pytest.raises(ValueError):
        decode_and_normalize_image(
            encoded,
            tmp_path / "assets" / "raw" / "beach_background.png",
            tmp_path / "assets" / "beach_background.png",
            "gpt-image-2",
            artifact_root=tmp_path,
        )


def test_decode_rejects_paths_that_disagree_with_artifact_metadata(tmp_path) -> None:
    with pytest.raises(ValueError, match="paths"):
        decode_and_normalize_image(
            _png_base64((32, 32)),
            tmp_path / "wrong-raw.png",
            tmp_path / "wrong-final.png",
            "gpt-image-2",
            artifact_root=tmp_path,
        )


def test_images_provider_rejects_timeout_below_spec_minimum() -> None:
    with pytest.raises(ValueError, match="120"):
        ImagesProvider(CONFIG, timeout=119)


def test_copy_overlay_assets_copies_all_verified_assets(tmp_path) -> None:
    copy_overlay_assets(SESSION2_ASSETS, tmp_path)
    assert {path.name for path in tmp_path.glob("*.png")} == {
        "lighthouse.png",
        "pet.png",
        "small_shelter.png",
    }


def test_response_evidence_replaces_base64_with_metadata() -> None:
    projected = response_evidence(
        {"created": 1, "data": [{"b64_json": "large-secret-payload"}]},
        decoded_bytes=123,
        raw_sha256="abc",
    )
    assert "large-secret-payload" not in json.dumps(projected)
    assert projected["data"][0]["b64_json"] == {
        "redacted": True,
        "decoded_bytes": 123,
        "raw_sha256": "abc",
        "saved_as": "assets/raw/beach_background.png",
    }
