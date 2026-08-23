"""Images API 调用、严格 PNG 解码与 512x288 确定性规范化。"""

from __future__ import annotations

import base64
import binascii
import io
import shutil
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image

from .config import ProviderConfig
from .external_models import CallRecord, ProviderCallError, _category, _error_text, _failure, _request_id, _response_json, resolve_endpoint
from .models import ImageArtifact
from .snapshot_builder import sha256_file

IMAGE_PROMPT = (
    "横向 2D 像素风海边场景背景，左侧有灯塔区域，中间留出宠物挥手活动区，"
    "右侧留出小窝位置，不要出现车辆，不要文字，适合作为游戏背景。"
)
OVERLAY_ASSET_IDS = ("lighthouse", "pet", "small_shelter")
MAX_BASE64_CHARS = 32 * 1024 * 1024
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 16_000_000


class ImagesProvider:
    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 180.0,
    ) -> None:
        if timeout < 120:
            raise ValueError("Images timeout must be at least 120 seconds")
        self.config = config
        self.transport = transport
        self.timeout = timeout

    def generate(self, prompt: str = IMAGE_PROMPT) -> tuple[str, CallRecord]:
        try:
            endpoint = resolve_endpoint(self.config.base_url, "images/generations")
        except ValueError as exc:
            raise ProviderCallError(
                _failure(
                    stage="images",
                    category="endpoint",
                    message=str(exc),
                    endpoint=self.config.base_url,
                    model=self.config.model,
                )
            ) from exc
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "n": 1,
        }
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                response = client.post(
                    endpoint,
                    headers={
                        "Authorization": "Bearer " + self.config.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            failure = _failure(
                stage="images",
                category="timeout",
                message="Images request timed out",
                endpoint=endpoint,
                model=self.config.model,
            )
            raise ProviderCallError(
                failure,
                calls={
                    "images": CallRecord(
                        endpoint=endpoint,
                        method="POST",
                        request=payload,
                        response=None,
                        http_status=None,
                        request_id=None,
                    )
                },
            ) from exc
        except httpx.RequestError as exc:
            failure = _failure(
                stage="images",
                category="endpoint",
                message="Images request failed before receiving a response: " + type(exc).__name__,
                endpoint=endpoint,
                model=self.config.model,
            )
            raise ProviderCallError(
                failure,
                calls={
                    "images": CallRecord(
                        endpoint=endpoint,
                        method="POST",
                        request=payload,
                        response=None,
                        http_status=None,
                        request_id=None,
                    )
                },
            ) from exc

        data = _response_json(response)
        call = CallRecord(
            endpoint=endpoint,
            method="POST",
            request=payload,
            response=data,
            http_status=response.status_code,
            request_id=_request_id(response),
        )
        if not response.is_success:
            raise ProviderCallError(
                _failure(
                    stage="images",
                    category=_category(response.status_code, data),
                    message="Images request failed: " + _error_text(data),
                    endpoint=endpoint,
                    model=self.config.model,
                    response=response,
                ),
                data,
                {"images": call},
            )
        try:
            items = data["data"]
            if not isinstance(items, list) or len(items) != 1:
                raise TypeError
            encoded = items[0]["b64_json"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderCallError(
                _failure(
                    stage="images",
                    category="decode",
                    message="Images response must contain exactly one b64_json item",
                    endpoint=endpoint,
                    model=self.config.model,
                    response=response,
                ),
                data,
                {"images": call},
            ) from exc
        if not isinstance(encoded, str) or not encoded:
            raise ProviderCallError(
                _failure(
                    stage="images",
                    category="decode",
                    message="Images b64_json is empty or not text",
                    endpoint=endpoint,
                    model=self.config.model,
                    response=response,
                ),
                data,
                {"images": call},
            )
        return encoded, call


def response_evidence(data: Any, decoded_bytes: int, raw_sha256: str) -> Any:
    """保留 Images 响应元数据，用不可逆摘要替换体积大的 Base64。"""
    if not isinstance(data, dict):
        return data
    projected = {key: data[key] for key in ("created", "model", "usage", "error") if key in data}
    projected["data"] = [
        {
            "b64_json": {
                "redacted": True,
                "decoded_bytes": decoded_bytes,
                "raw_sha256": raw_sha256,
                "saved_as": "assets/raw/beach_background.png",
            }
        }
    ]
    return projected


def decode_and_normalize_image(
    encoded: str,
    raw_path: Path,
    normalized_path: Path,
    model: str,
    *,
    artifact_root: Path,
) -> ImageArtifact:
    """严格解码原始 PNG，中心裁剪到 16:9，再输出 512x288 PNG。"""
    expected_raw = artifact_root / "assets" / "raw" / "beach_background.png"
    expected_normalized = artifact_root / "assets" / "beach_background.png"
    if raw_path.resolve() != expected_raw.resolve() or normalized_path.resolve() != expected_normalized.resolve():
        raise ValueError("image artifact paths do not match the declared relative metadata")
    if len(encoded) > MAX_BASE64_CHARS:
        raise ValueError("Images b64_json exceeds the configured size limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Images b64_json is not valid Base64") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("decoded Images PNG exceeds the configured size limit")

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG":
                raise ValueError("Images bytes are not a PNG")
            original_width, original_height = image.size
            if (
                original_width <= 0
                or original_height <= 0
                or original_width > MAX_IMAGE_DIMENSION
                or original_height > MAX_IMAGE_DIMENSION
                or original_width * original_height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("Images PNG dimensions exceed the configured pixel limit")
            image.load()
            rgb = image.convert("RGB")
    except (OSError, ValueError) as exc:
        raise ValueError("Pillow could not reopen Images PNG") from exc

    matrix = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if matrix is None or matrix.size == 0:
        raise ValueError("OpenCV could not decode Images PNG")

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)

    target_ratio = 16 / 9
    source_ratio = original_width / original_height
    if source_ratio > target_ratio:
        crop_width = round(original_height * target_ratio)
        left = (original_width - crop_width) // 2
        box = (left, 0, left + crop_width, original_height)
    else:
        crop_height = round(original_width / target_ratio)
        top = (original_height - crop_height) // 2
        box = (0, top, original_width, top + crop_height)
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    normalized = rgb.crop(box).resize((512, 288), resampling)
    normalized.save(normalized_path, format="PNG")

    with Image.open(normalized_path) as check:
        check.load()
        if check.format != "PNG" or check.size != (512, 288):
            raise ValueError("normalized image is not a 512x288 PNG")
        channels = len(check.getbands())
    if cv2.imread(str(normalized_path), cv2.IMREAD_UNCHANGED) is None:
        raise ValueError("OpenCV could not reopen normalized PNG")

    return ImageArtifact(
        asset_id="beach_background",
        model=model,
        raw_filename="assets/raw/beach_background.png",
        filename="assets/beach_background.png",
        uri="/assets/beach_background.png",
        original_width=original_width,
        original_height=original_height,
        normalized_width=512,
        normalized_height=288,
        channels=channels,
        raw_sha256=sha256_file(raw_path),
        sha256=sha256_file(normalized_path),
    )


def copy_overlay_assets(source_dir: Path, target_dir: Path) -> None:
    """自动复制阶段二已验证 overlay，避免手工文件操作绕过流水线。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    for asset_id in OVERLAY_ASSET_IDS:
        source = source_dir / f"{asset_id}.png"
        if not source.is_file():
            raise ValueError("verified overlay asset is missing: " + asset_id)
        shutil.copy2(source, target_dir / source.name)
