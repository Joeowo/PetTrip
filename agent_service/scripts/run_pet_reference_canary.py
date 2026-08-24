"""隔离验证固定宠物参考图在 65535 图片协议中的实际效果。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from agent_service.adapters.async_image_task import AsyncImageTaskClient, AsyncImageTaskRequest
from agent_service.adapters.image import ImageEditRequest, ImageReference, OpenAICompatibleImageProvider
from agent_service.shared.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "outputs" / "pet-reference-canary-20260824"
ASSET = ROOT / "data" / "reference_assets" / "pet" / "chongwu-bottom.png"
APERTURE = ROOT / "outputs" / "real-provider-semantic-v5" / "scene_1_aperture.png"
MASK = ROOT / "outputs" / "real-provider-semantic-v5" / "scene_1_mask.png"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_reference(data: bytes) -> ImageReference:
    from PIL import Image
    from io import BytesIO
    with Image.open(BytesIO(data)) as image:
        width, height = image.size
        mime = Image.MIME.get(image.format or "PNG", "image/png")
    return ImageReference(
        role="pet_reference",
        file_id="pet/chongwu-bottom.png",
        mime_type=mime,
        width=width,
        height=height,
        sha256=sha256(data),
        data=data,
        order_index=0,
    )


def write_json(name: str, value: dict) -> None:
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_sync(settings, aperture: bytes, mask: bytes, reference: ImageReference) -> dict:
    prompt = (
        "Edit only the masked circular area. The attached image is the canonical pet identity reference. "
        "Use the exact same character design, colors, silhouette, ears, tail, facial features, and proportions; "
        "do not reinterpret it as a cat, dog, realistic animal, or any other character. "
        "Place this fixed character walking on the beach with a curious happy expression. "
        "Keep every unmasked pixel unchanged and remove the black circle."
    )
    provider = OpenAICompatibleImageProvider(
        base_url=settings.image_base_url,
        api_key=settings.image_api_key,
        model=settings.image_model,
        timeout_seconds=settings.image_timeout,
        request_size="2048x1152",
        max_decoded_bytes=settings.image_max_decoded_bytes,
        max_image_pixels=settings.image_max_pixels,
    )
    try:
        result = await provider.edit(ImageEditRequest(
            image=aperture,
            mask=mask,
            prompt=prompt,
            size="2048x1152",
            references=(reference,),
        ))
        (EVIDENCE / "sync_standard_image_array.png").write_bytes(result.data)
        return {
            "protocol": "sync_standard_image_array",
            "endpoint": f"{settings.image_base_url}/images/edits",
            "status": "succeeded",
            "request_summary": {
                "multipart_fields": ["image[] (aperture)", "image[] (pet reference)", "mask"],
                "form_fields": ["model", "prompt", "n", "size", "response_format"],
                "reference_sha256": reference.sha256,
                "reference_file_id": reference.file_id,
            },
            "response": {"sha256": sha256(result.data), "width": result.width, "height": result.height},
        }
    except Exception as exc:
        return {
            "protocol": "sync_standard_image_array",
            "endpoint": f"{settings.image_base_url}/images/edits",
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "request_summary": {"multipart_fields": ["image[] (aperture)", "image[] (pet reference)", "mask"], "form_fields": ["model", "prompt", "n", "size", "response_format"], "reference_sha256": reference.sha256},
        }


def run_async(settings, aperture: bytes, mask: bytes, reference: ImageReference) -> dict:
    prompt = (
        "Edit the black circle only. The attached reference is the canonical fixed pet character. "
        "Preserve its exact design and place it walking on the beach, curious and happy; do not render a cat or dog. "
        "Keep the unmasked environment unchanged."
    )
    client = AsyncImageTaskClient(
        base_url=settings.image_base_url,
        api_key=settings.image_api_key,
        timeout=settings.image_timeout,
        poll_interval=2.0,
        max_poll_attempts=180,
    )
    request = AsyncImageTaskRequest(
        model=settings.image_model,
        prompt=prompt,
        image_bytes=aperture,
        mask_bytes=mask,
        size="2048x1152",
        quality="high",
        n=1,
        idempotency_key="pet-reference-canary-async-20260824",
        references=(reference,),
    )
    try:
        result = client.submit_and_wait(request)
        (EVIDENCE / "async_standard_image_array.png").write_bytes(result.data)
        return {
            "protocol": "async_current_inputs_references",
            "endpoint": f"{settings.image_base_url}/v1/tasks",
            "status": "succeeded",
            "request_summary": {
                "type": "image.edit",
                "parameter_fields": ["prompt", "size", "quality", "n"],
                "input_fields": ["image", "mask", "references"],
                "reference_sha256": reference.sha256,
                "reference_file_id": reference.file_id,
            },
            "response": {"sha256": sha256(result.data), "width": result.width, "height": result.height},
        }
    except Exception as exc:
        return {
            "protocol": "async_current_inputs_references",
            "endpoint": f"{settings.image_base_url}/v1/tasks",
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "request_summary": {"type": "image.edit", "parameter_fields": ["prompt", "size", "quality", "n"], "input_fields": ["image", "mask", "references"], "reference_sha256": reference.sha256},
        }


async def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    settings = load_settings(overrides={"PILOT_API_KEY": "local-canary-only"})
    asset = ASSET.read_bytes()
    aperture = APERTURE.read_bytes()
    mask = MASK.read_bytes()
    reference = make_reference(asset)
    write_json("inputs.json", {"asset": str(ASSET), "asset_sha256": reference.sha256, "asset_bytes": len(asset), "aperture_sha256": sha256(aperture), "mask_sha256": sha256(mask), "size": "2048x1152"})
    sync_result = await run_sync(settings, aperture, mask, reference)
    write_json("sync_result.json", sync_result)
    write_json("summary.json", {"sync": sync_result})
    print(json.dumps({"sync_status": sync_result["status"], "evidence_dir": str(EVIDENCE)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
