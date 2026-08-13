"""受控本地图片存储。

上传图片只在内存中完成有界读取和 Pillow 校验，随后原子写入 ``files/input``。
SQLite 只接收本模块返回的相对路径和元数据，不接收图片二进制或 Base64。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import (
    FILE_DECODE_FAILED,
    FILE_TOO_LARGE,
    FILE_TYPE_UNSUPPORTED,
    ApiError,
)

_FORMATS = {
    "PNG": ("image/png", {".png"}),
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
}


@dataclass(frozen=True)
class StoredImage:
    rel_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    width: int
    height: int


class LocalFileIntegrityError(RuntimeError):
    """已登记的本地图片缺失、损坏或与元数据不一致。"""


class LocalImageStorage:
    """在固定数据根目录内验证、保存和读取输入图片。"""

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir).resolve()
        self._input_dir = self._data_dir / "files" / "input"
        self._generated_dir = self._data_dir / "files" / "generated"
        self._input_dir.mkdir(parents=True, exist_ok=True)
        self._generated_dir.mkdir(parents=True, exist_ok=True)

    def validate_and_store(
        self,
        *,
        file_id: str,
        filename: str,
        declared_mime_type: str,
        data: bytes,
        max_bytes: int,
        max_dimension: int,
        max_pixels: int,
    ) -> StoredImage:
        if len(data) > max_bytes:
            raise ApiError(FILE_TOO_LARGE, "图片文件超过允许大小。")
        if not data:
            raise ApiError(FILE_DECODE_FAILED, "图片文件为空或无法解码。")

        extension = Path(filename).suffix.lower()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    actual_format = image.format
                    width, height = image.size
                    if (
                        width > max_dimension
                        or height > max_dimension
                        or width * height > max_pixels
                    ):
                        raise ApiError(
                            FILE_TOO_LARGE,
                            "图片尺寸或像素数量超过允许范围。",
                        )
                    image.verify()
                with Image.open(BytesIO(data)) as decoded:
                    decoded.load()
        except Image.DecompressionBombError as exc:
            raise ApiError(FILE_TOO_LARGE, "图片像素数量超过允许范围。") from exc
        except Image.DecompressionBombWarning as exc:
            raise ApiError(FILE_TOO_LARGE, "图片像素数量超过允许范围。") from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ApiError(FILE_DECODE_FAILED, "图片无法解码。") from exc

        format_contract = _FORMATS.get(actual_format or "")
        if format_contract is None:
            raise ApiError(FILE_TYPE_UNSUPPORTED, "只支持 PNG 和 JPEG 图片。")
        actual_mime_type, allowed_extensions = format_contract
        normalized_declared_type = declared_mime_type.lower().split(";", 1)[0].strip()
        if (
            normalized_declared_type != actual_mime_type
            or extension not in allowed_extensions
        ):
            raise ApiError(
                FILE_TYPE_UNSUPPORTED,
                "文件扩展名、声明类型与实际图片格式不一致。",
            )
        target = self._input_dir / f"{file_id}{extension}"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{file_id}-",
            suffix=".tmp",
            dir=self._input_dir,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

        return StoredImage(
            rel_path=target.relative_to(self._data_dir).as_posix(),
            mime_type=actual_mime_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            width=width,
            height=height,
        )

    def normalize_and_store_generated(
        self,
        *,
        file_id: str,
        data: bytes,
        target_width: int,
        target_height: int,
        max_pixels: int,
    ) -> StoredImage:
        """Normalize a validated Provider image to a PNG canvas and atomically store it."""
        if target_width <= 0 or target_height <= 0:
            raise ValueError("目标画布尺寸必须为正数。")
        if target_width * target_height > max_pixels:
            raise ValueError("目标画布像素数量超过允许范围。")
        try:
            with Image.open(BytesIO(data)) as source:
                source.load()
                image = ImageOps.fit(
                    source.convert("RGBA"),
                    (target_width, target_height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
                output = BytesIO()
                image.save(output, format="PNG", optimize=False)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise LocalFileIntegrityError("生成图片无法规范化。") from exc

        normalized = output.getvalue()
        target = self._generated_dir / f"{file_id}.png"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{file_id}-",
            suffix=".tmp",
            dir=self._generated_dir,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(normalized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return StoredImage(
            rel_path=target.relative_to(self._data_dir).as_posix(),
            mime_type="image/png",
            size_bytes=len(normalized),
            sha256=hashlib.sha256(normalized).hexdigest(),
            width=target_width,
            height=target_height,
        )

    def store_bytes(self, file_id: str, data: bytes) -> str:
        """为内部测试保存不会覆盖既有资源的受控字节。"""
        target = self._input_dir / f"{file_id}.bin"
        with target.open("xb") as stream:
            stream.write(data)
        return target.relative_to(self._data_dir).as_posix()

    def resolve(self, rel_path: str) -> Path:
        candidate = (self._data_dir / rel_path).resolve()
        try:
            candidate.relative_to(self._data_dir)
        except ValueError as exc:
            raise ValueError("文件路径超出存储根目录。") from exc
        return candidate

    def read_verified(self, file_row: dict[str, object]) -> bytes:
        try:
            data = self.resolve(str(file_row["rel_path"])).read_bytes()
        except (OSError, ValueError) as exc:
            raise LocalFileIntegrityError("图片文件缺失或不可读。") from exc
        if hashlib.sha256(data).hexdigest() != file_row["sha256"]:
            raise LocalFileIntegrityError("图片内容哈希与文件记录不一致。")
        try:
            with Image.open(BytesIO(data)) as image:
                actual_format = image.format or ""
                width, height = image.size
                image.load()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise LocalFileIntegrityError("图片文件已损坏。") from exc
        contract = _FORMATS.get(actual_format)
        if (
            contract is None
            or contract[0] != file_row["mime_type"]
            or width != file_row["width"]
            or height != file_row["height"]
        ):
            raise LocalFileIntegrityError("图片格式或尺寸与文件记录不一致。")
        return data

    def read_bytes(self, rel_path: str) -> bytes:
        return self.resolve(rel_path).read_bytes()

    def delete(self, rel_path: str) -> None:
        self.resolve(rel_path).unlink(missing_ok=True)

    def remove_untracked_files(self, tracked_rel_paths: set[str]) -> int:
        """删除输入和生成目录中没有 SQLite 元数据记录的孤儿文件。"""
        removed = 0
        for directory in (self._input_dir, self._generated_dir):
            for path in directory.iterdir():
                if not path.is_file() or path.name.startswith("."):
                    continue
                rel_path = path.relative_to(self._data_dir).as_posix()
                if rel_path not in tracked_rel_paths:
                    path.unlink()
                    removed += 1
        return removed
