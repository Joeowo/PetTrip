"""Agent 面板 Snapshot 的稳定 identity、canonical hash 和边界规则。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


SNAPSHOT_SCHEMA_NAME = "agent_panel_snapshot"
SUPPORTED_SNAPSHOT_SCHEMAS = frozenset({(SNAPSHOT_SCHEMA_NAME, "0.1")})


class SnapshotSchemaError(ValueError):
    """面板 Snapshot schema 未注册或版本不兼容。"""


@dataclass(frozen=True, slots=True)
class SnapshotIdentity:
    """绑定 Destination Spec 的不可变公开身份。"""

    destination_id: str
    spec_id: str
    spec_version: int
    spec_sha256: str

    def __post_init__(self) -> None:
        if not self.destination_id or not self.spec_id:
            raise ValueError("Snapshot identity 必须包含 destination_id 和 spec_id。")
        if self.spec_version < 1:
            raise ValueError("spec_version 必须为正整数。")
        if len(self.spec_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.spec_sha256
        ):
            raise ValueError("spec_sha256 必须是小写十六进制 SHA-256。")

    def as_dict(self) -> dict[str, Any]:
        return {
            "destination_id": self.destination_id,
            "spec_id": self.spec_id,
            "spec_version": self.spec_version,
            "spec_sha256": self.spec_sha256,
        }


def _without_excluded_fields(value: Any, excluded_fields: set[str]) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_excluded_fields(item, excluded_fields)
            for key, item in value.items()
            if key not in excluded_fields
        }
    if isinstance(value, list):
        return [_without_excluded_fields(item, excluded_fields) for item in value]
    if isinstance(value, tuple):
        return [_without_excluded_fields(item, excluded_fields) for item in value]
    return value


def canonical_json_bytes(
    value: Mapping[str, Any], *, excluded_fields: set[str] | None = None
) -> bytes:
    """按面板契约将 JSON-compatible mapping 编码成稳定 UTF-8 字节。"""
    normalized = _without_excluded_fields(value, excluded_fields or set())
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def snapshot_sha256(
    value: Mapping[str, Any], *, excluded_fields: set[str] | None = None
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value, excluded_fields=excluded_fields)
    ).hexdigest()


def snapshot_etag(
    value: Mapping[str, Any], *, excluded_fields: set[str] | None = None
) -> str:
    return f'"{snapshot_sha256(value, excluded_fields=excluded_fields)}"'


def validate_snapshot_schema(schema_name: str, schema_version: str) -> None:
    if (schema_name, schema_version) not in SUPPORTED_SNAPSHOT_SCHEMAS:
        raise SnapshotSchemaError(
            f"不支持的 Snapshot schema: {schema_name}/{schema_version}"
        )


def public_bottom_left_center(
    center_x: int, center_y_top_left: int, *, canvas_height_px: int
) -> dict[str, int]:
    """将内部 pixel_top_left 圆心转换为公开 pixel_bottom_left 圆心。"""
    if canvas_height_px <= 0:
        raise ValueError("canvas_height_px 必须为正整数。")
    if not 0 <= center_x < canvas_height_px * 10:
        raise ValueError("center_x 超出有效画布范围。")
    if not 0 <= center_y_top_left < canvas_height_px:
        raise ValueError("center_y_top_left 超出有效画布范围。")
    return {"x": center_x, "y": canvas_height_px - center_y_top_left}
