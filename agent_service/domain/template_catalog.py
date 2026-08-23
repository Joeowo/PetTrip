"""运行时模板目录与受约束的环境 Prompt 渲染。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class TemplateError(ValueError):
    """模板缺失、格式非法或渲染输入违反模板约束。"""


@dataclass(frozen=True)
class TemplateReference:
    role: Literal["style_reference", "composition_reference"]
    asset_key: str
    order_index: int


@dataclass(frozen=True)
class ImageTemplate:
    template_id: str
    version: str
    name: str
    prompt: str
    allowed_slots: tuple[str, ...]
    required_slots: tuple[str, ...]
    negative_constraints: tuple[str, ...]
    references: tuple[TemplateReference, ...]


@dataclass(frozen=True)
class RenderedEnvironmentTemplate:
    style_template_id: str
    style_template_version: str
    composition_template_id: str
    composition_template_version: str
    filled_slots: dict[str, str]
    negative_constraints: tuple[str, ...]
    references: tuple[TemplateReference, ...]
    prompt: str


class TemplateCatalog:
    """从 JSON 文件加载并渲染画风与构图模板。"""

    DEFAULT_VERSION = "1.0"

    def __init__(
        self,
        style_path: str | Path,
        composition_path: str | Path,
        asset_root: str | Path | None = None,
    ) -> None:
        self._asset_root = Path(asset_root) if asset_root is not None else None
        self._asset_manifest = self._load_asset_manifest()
        self._styles = self._load(Path(style_path), "style")
        self._compositions = self._load(Path(composition_path), "composition")

    @classmethod
    def default(cls) -> "TemplateCatalog":
        template_dir = Path(__file__).resolve().parents[1] / "data" / "templates"
        asset_root = template_dir.parent / "reference_assets"
        return cls(
            template_dir / "style_templates.json",
            template_dir / "composition_templates.json",
            asset_root,
        )

    def load_reference(self, asset_key: str) -> dict[str, Any]:
        """读取并校验一个已登记参考资产，失败时不返回不可信字节。"""
        if self._asset_root is None:
            raise TemplateError("当前模板目录未配置参考资产目录")
        metadata = self._asset_manifest.get(asset_key)
        if metadata is None:
            raise TemplateError(f"参考资产未登记: {asset_key}")
        path = self._asset_root / asset_key
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise TemplateError(f"参考资产缺失: {asset_key}") from exc
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise TemplateError(f"参考资产 SHA-256 不匹配: {asset_key}")
        return {**metadata, "data": data}

    def _load_asset_manifest(self) -> dict[str, dict[str, Any]]:
        if self._asset_root is None:
            return {}
        manifest_path = self._asset_root / "manifest.json"
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            assets = raw["assets"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise TemplateError(f"无法加载参考资产清单: {manifest_path}") from exc
        if not isinstance(assets, list):
            raise TemplateError("参考资产清单 assets 必须是数组")
        manifest: dict[str, dict[str, Any]] = {}
        for asset in assets:
            if not isinstance(asset, dict) or not isinstance(asset.get("asset_key"), str):
                raise TemplateError("参考资产清单包含非法记录")
            key = asset["asset_key"]
            if key in manifest:
                raise TemplateError(f"参考资产 key 重复: {key}")
            manifest[key] = asset
        return manifest

    def get_style(self, template_id: str) -> ImageTemplate:
        return self._get(self._styles, template_id, "画风")

    def get_composition(self, template_id: str) -> ImageTemplate:
        return self._get(self._compositions, template_id, "构图")

    def render_environment(
        self,
        *,
        style_template_id: str,
        composition_template_id: str,
        slot_values: dict[str, str],
    ) -> RenderedEnvironmentTemplate:
        style = self.get_style(style_template_id)
        composition = self.get_composition(composition_template_id)
        allowed = set(style.allowed_slots) | set(composition.allowed_slots)
        undeclared = sorted(set(slot_values) - allowed)
        if undeclared:
            raise TemplateError(f"包含未声明槽位: {', '.join(undeclared)}")

        required = set(style.required_slots) | set(composition.required_slots)
        missing = sorted(name for name in required if not slot_values.get(name, "").strip())
        if missing:
            raise TemplateError(f"缺少必填槽位: {', '.join(missing)}")

        filled_slots = {
            name: value.strip()
            for name, value in slot_values.items()
            if value.strip()
        }
        negative_constraints = (
            style.negative_constraints + composition.negative_constraints
        )
        references = tuple(
            sorted(
                style.references + composition.references,
                key=lambda reference: (reference.order_index, reference.role),
            )
        )
        prompt_parts = [style.prompt, composition.prompt]
        slot_labels = {"destination_description": "目的地描述"}
        for name in sorted(filled_slots):
            prompt_parts.append(f"{slot_labels.get(name, name)}：{filled_slots[name]}")
        if negative_constraints:
            prompt_parts.append(f"负向约束：{'；'.join(negative_constraints)}")

        return RenderedEnvironmentTemplate(
            style_template_id=style.template_id,
            style_template_version=style.version,
            composition_template_id=composition.template_id,
            composition_template_version=composition.version,
            filled_slots=filled_slots,
            negative_constraints=negative_constraints,
            references=references,
            prompt="\n".join(prompt_parts),
        )

    def _load(
        self, path: Path, kind: Literal["style", "composition"]
    ) -> dict[str, ImageTemplate]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TemplateError(f"无法加载模板文件: {path}") from exc
        if not isinstance(raw, list):
            raise TemplateError(f"模板文件必须是数组: {path}")

        templates: dict[str, ImageTemplate] = {}
        for index, record in enumerate(raw):
            if not isinstance(record, dict):
                raise TemplateError(f"模板记录必须是对象: {path}#{index}")
            template = self._parse_record(record, kind, path, index)
            if template is None:
                continue
            if template.template_id in templates:
                raise TemplateError(f"模板 ID 重复: {template.template_id}")
            templates[template.template_id] = template
        if not templates:
            raise TemplateError(f"模板文件没有有效记录: {path}")
        return templates

    def _parse_record(
        self,
        record: dict[str, Any],
        kind: Literal["style", "composition"],
        path: Path,
        index: int,
    ) -> ImageTemplate | None:
        template_id = record.get("id")
        if not isinstance(template_id, str) or not template_id.strip():
            raise TemplateError(f"模板缺少 ID: {path}#{index}")

        if kind == "composition" and record.get("field_2") == "构图名称":
            return None

        if "prompt" in record:
            name = record.get("name")
            prompt = record.get("prompt")
            version = record.get("version")
            allowed_slots = self._string_list(record, "allowed_slots", path, index)
            required_slots = self._string_list(record, "required_slots", path, index)
            negative_constraints = self._string_list(
                record, "negative_constraints", path, index
            )
            references = self._parse_references(record.get("references"), kind, path, index)
        elif kind == "style":
            name = record.get("画风名称")
            prompt = record.get("详细提示词")
            version = record.get("version", self.DEFAULT_VERSION)
            allowed_slots = ("destination_description",)
            required_slots = ("destination_description",)
            negative_constraints = ()
            references = self._legacy_style_references(template_id, record)
        else:
            name = record.get("field_2")
            prompt = record.get("field_6")
            version = record.get("version", self.DEFAULT_VERSION)
            allowed_slots = ()
            required_slots = ()
            negative_constraints = ()
            references = ()

        if not isinstance(version, str) or not version.strip():
            raise TemplateError(f"模板版本非法: {template_id}")
        if not isinstance(name, str) or not name.strip():
            raise TemplateError(f"模板名称非法: {template_id}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise TemplateError(f"模板 Prompt 非法: {template_id}")
        if not set(required_slots).issubset(allowed_slots):
            raise TemplateError(f"必填槽位未在 allowed_slots 声明: {template_id}")

        return ImageTemplate(
            template_id=template_id,
            version=version,
            name=name.strip(),
            prompt=prompt.strip(),
            allowed_slots=allowed_slots,
            required_slots=required_slots,
            negative_constraints=negative_constraints,
            references=references,
        )

    @staticmethod
    def _string_list(
        record: dict[str, Any], key: str, path: Path, index: int
    ) -> tuple[str, ...]:
        value = record.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise TemplateError(f"{key} 必须是非空字符串数组: {path}#{index}")
        return tuple(item.strip() for item in value)

    @staticmethod
    def _parse_references(
        value: Any,
        kind: Literal["style", "composition"],
        path: Path,
        index: int,
    ) -> tuple[TemplateReference, ...]:
        if not isinstance(value, list):
            raise TemplateError(f"references 必须是数组: {path}#{index}")
        role = "style_reference" if kind == "style" else "composition_reference"
        references: list[TemplateReference] = []
        for order_index, item in enumerate(value):
            asset_key = item.get("asset_key") if isinstance(item, dict) else item
            if not isinstance(asset_key, str) or not asset_key.strip():
                raise TemplateError(f"参考资产非法: {path}#{index}")
            references.append(
                TemplateReference(
                    role=role,
                    asset_key=asset_key.strip(),
                    order_index=order_index,
                )
            )
        return tuple(references)

    @staticmethod
    def _legacy_style_references(
        template_id: str, record: dict[str, Any]
    ) -> tuple[TemplateReference, ...]:
        references = []
        for order_index in range(3):
            value = record.get(f"画面参考图{order_index + 1}")
            if value == "原图链接" and template_id.startswith("style_"):
                value = f"{template_id}/ref_{order_index + 1}.png"
            if isinstance(value, str) and value.strip():
                references.append(
                    TemplateReference(
                        role="style_reference",
                        asset_key=value.strip(),
                        order_index=order_index,
                    )
                )
        return tuple(references)

    @staticmethod
    def _get(
        templates: dict[str, ImageTemplate], template_id: str, label: str
    ) -> ImageTemplate:
        try:
            return templates[template_id]
        except KeyError as exc:
            raise TemplateError(f"{label}模板不存在: {template_id}") from exc
