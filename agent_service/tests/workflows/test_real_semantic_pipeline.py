from __future__ import annotations

import json

from agent_service.adapters.llm import ChatMessage
from agent_service.shared.structured_output import StructuredOutputRegistry


class SemanticProvider:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def complete(self, messages: list[ChatMessage]) -> str:
        return "请继续补充；我会保留橘猫、海边、灯塔和夕阳。"

    async def complete_structured(self, messages, request) -> str:
        content = "\n".join(message.content for message in messages)
        self.requests.append((request.schema_name, content))
        if request.schema_name == "clarification_turn":
            return json.dumps({
                "type": "clarification_turn",
                "schema_version": "1.0",
                "classification": "accepted_wish_input",
                "normalized_text": "温顺橘猫去海边灯塔旅行，夕阳下散步和休息",
                "assistant_reply": "请确认两个场景分别是海边散步和灯塔旁休息。",
                "captured_facts": ["橘猫", "海边", "灯塔", "夕阳"],
                "missing_dimensions": [],
                "close_recommendation": True,
            }, ensure_ascii=False)
        if request.schema_name == "destination_requirements":
            try:
                source = json.loads(messages[-1].content)
                ids = [item["input_id"] for item in source]
            except (json.JSONDecodeError, TypeError, KeyError):
                ids = ["input-1"]
            return json.dumps({
                "type": "destination_requirements",
                "schema_version": "1.0",
                "items": [{
                    "normalized_statement": "温顺橘猫在夕阳海边灯塔旅行",
                    "polarity": "include",
                    "fulfillment": "must_satisfy",
                    "source_type": "player_input",
                    "source_input_ids": ids,
                    "rationale": None,
                }],
            }, ensure_ascii=False)
        if request.schema_name == "environment_template_selection":
            return json.dumps({
                "type": "environment_template_selection",
                "schema_version": "1.0",
                "style_template_id": "style_001",
                "composition_template_id": "composition_002",
                "rationale": "突出灯塔与夕阳海岸",
            }, ensure_ascii=False)
        return json.dumps({
            "type": "destination_spec",
            "schema_version": "1.0",
            "template_id": "default_pet_destination",
            "template_version": "1.0",
            "title": "橘猫的夕阳灯塔海岸",
            "shared_environment_spec": {
                "description": "温暖夕阳下的海边灯塔与沙滩",
                "style_constraints": ["温馨自然"],
                "composition_constraints": ["灯塔为主要地标"],
                "negative_constraints": ["不要室内小屋"],
            },
            "scene_plans": [
                {"order": 0, "state_label": "散步", "pet_behavior": "沿沙滩散步", "pet_emotion": "开心", "semantic_anchor": "夕阳灯塔前的海岸线", "interaction_prompt": "陪橘猫在海边散步"},
                {"order": 1, "state_label": "休息", "pet_behavior": "在灯塔旁休息", "pet_emotion": "放松", "semantic_anchor": "灯塔基座旁的温暖沙地", "interaction_prompt": "抚摸休息中的橘猫"},
            ],
        }, ensure_ascii=False)


def test_real_structured_contracts_preserve_seaside_cat_semantics() -> None:
    provider = SemanticProvider()
    registry = StructuredOutputRegistry()
    text = "我想带一只温顺橘猫去海边灯塔旅行，在夕阳下散步和休息"
    for schema_name in (
        "clarification_turn",
        "destination_requirements",
        "environment_template_selection",
        "destination_spec",
    ):
        request = registry.request_for(schema_name=schema_name, schema_version="1.0")
        raw = __import__("asyncio").run(
            provider.complete_structured([ChatMessage(role="user", content=text)], request)
        )
        parsed = registry.parse_and_validate(
            raw, schema_name=schema_name, schema_version="1.0"
        )
        serialized = json.dumps(parsed, ensure_ascii=False)
        if schema_name in {"clarification_turn", "destination_requirements", "destination_spec"}:
            assert "橘猫" in serialized
        if schema_name == "destination_spec":
            assert "海边" in serialized
            assert "灯塔" in serialized
            assert "夕阳" in serialized
            assert "温馨旅行小屋" not in serialized
