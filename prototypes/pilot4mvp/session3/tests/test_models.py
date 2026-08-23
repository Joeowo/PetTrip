from __future__ import annotations

import pytest
from pydantic import ValidationError

from content_service.models import WorldSpec


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


def test_world_spec_accepts_complete_fixed_scene() -> None:
    spec = WorldSpec.model_validate(VALID_WORLD)
    assert spec.interaction_id == "pet_wave"
    assert spec.forbidden_objects == ["vehicle"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.pop("interaction_id"),
        lambda data: data.update({"landmark": "car"}),
        lambda data: data.update({"canvas_width": 1024}),
        lambda data: data.update({"extra": "forbidden"}),
        lambda data: data.update({"forbidden_objects": []}),
    ],
)
def test_world_spec_rejects_missing_wrong_or_extra_fields(mutation) -> None:
    data = dict(VALID_WORLD)
    mutation(data)
    with pytest.raises(ValidationError):
        WorldSpec.model_validate(data)


def test_world_spec_schema_requires_every_field() -> None:
    schema = WorldSpec.model_json_schema()
    assert set(schema["required"]) == set(VALID_WORLD)
    assert schema["additionalProperties"] is False
