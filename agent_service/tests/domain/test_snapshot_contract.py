from __future__ import annotations

import pytest

from agent_service.domain.snapshot import (
    SnapshotIdentity,
    SnapshotSchemaError,
    canonical_json_bytes,
    public_bottom_left_center,
    snapshot_etag,
    snapshot_sha256,
    validate_snapshot_schema,
)


def test_canonical_json_is_stable_across_mapping_order_and_unicode() -> None:
    first = {"title": "海边散步", "items": [1, 2], "nullable": None}
    second = {"nullable": None, "items": [1, 2], "title": "海边散步"}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_bytes(first).decode("utf-8") == (
        '{"items":[1,2],"nullable":null,"title":"海边散步"}'
    )
    assert snapshot_sha256(first) == snapshot_sha256(second)


def test_snapshot_hash_excludes_dynamic_projection_fields() -> None:
    payload = {
        "snapshot_id": "snapshot_1",
        "request_id": "req_1",
        "assets": [{"download_url": "/api/v1/files/file_1/content", "sha256": "a" * 64}],
    }
    changed = {
        **payload,
        "request_id": "req_2",
        "assets": [{"download_url": "/different", "sha256": "a" * 64}],
    }

    assert snapshot_sha256(payload, excluded_fields={"request_id", "download_url"}) == snapshot_sha256(
        changed, excluded_fields={"request_id", "download_url"}
    )
    assert snapshot_etag(payload, excluded_fields={"request_id", "download_url"}).startswith('"')


def test_snapshot_identity_is_immutable_and_serializable() -> None:
    identity = SnapshotIdentity(
        destination_id="destination_1",
        spec_id="spec_1",
        spec_version=2,
        spec_sha256="b" * 64,
    )

    assert identity.as_dict() == {
        "destination_id": "destination_1",
        "spec_id": "spec_1",
        "spec_version": 2,
        "spec_sha256": "b" * 64,
    }
    with pytest.raises((AttributeError, TypeError)):
        identity.spec_version = 3  # type: ignore[misc]


def test_unknown_snapshot_schema_fails_closed() -> None:
    validate_snapshot_schema("agent_panel_snapshot", "0.1")

    with pytest.raises(SnapshotSchemaError):
        validate_snapshot_schema("agent_panel_snapshot", "9.9")


def test_coordinates_convert_from_top_left_to_bottom_left_once() -> None:
    assert public_bottom_left_center(12, 7, canvas_height_px=48) == {"x": 12, "y": 41}

    with pytest.raises(ValueError):
        public_bottom_left_center(12, 49, canvas_height_px=48)
