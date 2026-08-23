import json

import pytest

from agent_service.domain.template_catalog import TemplateCatalog, TemplateError


def _write_catalog(path, records):
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def test_environment_prompt_is_rendered_from_runtime_template_files(tmp_path):
    style_path = tmp_path / "styles.json"
    composition_path = tmp_path / "compositions.json"
    _write_catalog(
        style_path,
        [
            {
                "id": "style_fixture",
                "version": "2.0",
                "name": "纸艺",
                "prompt": "使用分层纸艺和柔和阴影。",
                "allowed_slots": ["destination_description"],
                "required_slots": ["destination_description"],
                "negative_constraints": ["不要照片写实"],
                "references": [],
            }
        ],
    )
    _write_catalog(
        composition_path,
        [
            {
                "id": "composition_fixture",
                "version": "3.0",
                "name": "对角线构图",
                "prompt": "主体沿左下到右上的对角线组织。",
                "allowed_slots": [],
                "required_slots": [],
                "negative_constraints": ["避免多焦点"],
                "references": [],
            }
        ],
    )

    rendered = TemplateCatalog(style_path, composition_path).render_environment(
        style_template_id="style_fixture",
        composition_template_id="composition_fixture",
        slot_values={"destination_description": "一间面朝森林的温馨旅屋"},
    )

    assert rendered.style_template_version == "2.0"
    assert rendered.composition_template_version == "3.0"
    assert rendered.filled_slots == {
        "destination_description": "一间面朝森林的温馨旅屋"
    }
    assert rendered.negative_constraints == ("不要照片写实", "避免多焦点")
    assert rendered.prompt == (
        "使用分层纸艺和柔和阴影。\n"
        "主体沿左下到右上的对角线组织。\n"
        "目的地描述：一间面朝森林的温馨旅屋\n"
        "负向约束：不要照片写实；避免多焦点"
    )

    records = json.loads(style_path.read_text(encoding="utf-8"))
    records[0]["prompt"] = "使用可见纸张纤维和层叠剪影。"
    _write_catalog(style_path, records)
    changed = TemplateCatalog(style_path, composition_path).render_environment(
        style_template_id="style_fixture",
        composition_template_id="composition_fixture",
        slot_values={"destination_description": "一间面朝森林的温馨旅屋"},
    )
    assert changed.prompt.startswith("使用可见纸张纤维和层叠剪影。")


def test_default_catalog_resolves_bundled_style_reference_assets():
    catalog = TemplateCatalog.default()

    rendered = catalog.render_environment(
        style_template_id="style_001",
        composition_template_id="composition_002",
        slot_values={"destination_description": "旅屋"},
    )

    assert [reference.asset_key for reference in rendered.references] == [
        "style_001/ref_1.png",
        "style_001/ref_2.png",
        "style_001/ref_3.png",
    ]
    asset = catalog.load_reference("style_001/ref_1.png")
    assert asset["role"] == "style_reference"
    assert asset["sha256"]
    assert asset["data"]


def test_environment_prompt_rejects_undeclared_slots(tmp_path):
    style_path = tmp_path / "styles.json"
    composition_path = tmp_path / "compositions.json"
    base = {
        "version": "1.0",
        "name": "fixture",
        "prompt": "固定提示词",
        "allowed_slots": [],
        "required_slots": [],
        "negative_constraints": [],
        "references": [],
    }
    _write_catalog(style_path, [{"id": "style_fixture", **base}])
    _write_catalog(composition_path, [{"id": "composition_fixture", **base}])

    catalog = TemplateCatalog(style_path, composition_path)
    with pytest.raises(TemplateError, match="未声明槽位"):
        catalog.render_environment(
            style_template_id="style_fixture",
            composition_template_id="composition_fixture",
            slot_values={"camera": "wide"},
        )
