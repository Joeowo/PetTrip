"""澄清与规格生成 LangGraph 工作流（T3 - issue #15）。

从玩家输入到锁定 DestinationSpec 的核心生成链路。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END
from ..storage.destination_storage import DestinationRepository
from ..shared.ids import new_id


# ============================================================================
# Graph State
# ============================================================================


class ClarificationSpecState(TypedDict):
    """工作流状态。

    checkpoint 只保存运行位置、ID、游标和必要临时引用，
    不保存完整领域对象副本（issue #10 第 7.3 节）。
    """

    # 输入
    session_id: str
    destination_id: str

    # 澄清阶段
    clarification_inputs: list[dict[str, Any]]  # 从 Repository 读取
    close_condition_met: bool

    # Requirements 生成
    requirements_id: str | None
    requirements_sha256: str | None
    wish_items: list[dict[str, Any]] | None  # 提取的愿望条目

    # 模板设计（Issue #36 - 2.2）
    style_template_id: str | None
    composition_template_id: str | None
    template_design_rationale: str | None  # LLM 选择推理

    # Spec 生成
    spec_id: str | None
    spec_sha256: str | None
    scene_plan_data: list[dict[str, Any]] | None  # 来自 LLM 的 scene_plans 数据
    scene_plan_ids: list[str] | None  # 两个 ScenePlan 的 ID

    # 错误处理
    error: str | None


# ============================================================================
# Mock LLM 函数（T3 阶段使用 fixture，issue #15 "快速主链路原则"）
# ============================================================================


def mock_extract_wish_items(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mock 提取愿望条目（真实实现会调用 LLM）。

    Returns:
        list[dict]: 每个条目包含
            - normalized_statement: str
            - polarity: "include" | "exclude"
            - fulfillment: "must_satisfy" | "best_effort" | "creative_discretion"
            - source_type: "player_input" | "agent_inference" | "template_default"
            - source_input_ids: list[str]
            - rationale: str | None (agent_inference 必须有)
    """
    items = []

    # 从 accepted_wish_input 中提取
    accepted_inputs = [
        inp for inp in inputs if inp["classification"] == "accepted_wish_input"
    ]

    for inp in accepted_inputs:
        # 玩家直接输入的愿望
        items.append({
            "normalized_statement": inp.get("normalized_text") or inp["raw_text"],
            "polarity": "include",
            "fulfillment": "best_effort",
            "source_type": "player_input",
            "source_input_ids": [inp["input_id"]],
            "rationale": None,
        })

    # 添加一个 agent_inference 示例（必须有 rationale）
    if accepted_inputs:
        items.append({
            "normalized_statement": "确保宠物在画面中清晰可见",
            "polarity": "include",
            "fulfillment": "must_satisfy",
            "source_type": "agent_inference",
            "source_input_ids": [inp["input_id"] for inp in accepted_inputs],
            "rationale": "基于玩家愿望推断，宠物应作为场景的核心交互对象",
        })

    # 添加模板默认项
    items.append({
        "normalized_statement": "温馨舒适的氛围",
        "polarity": "include",
        "fulfillment": "creative_discretion",
        "source_type": "template_default",
        "source_input_ids": [],
        "rationale": None,
    })

    return items


def mock_generate_destination_spec(
    requirements_id: str, requirements_sha256: str
) -> dict[str, Any]:
    """Mock 生成目的地规格（真实实现会调用 LLM）。

    Returns:
        dict: 包含
            - template_id: str
            - template_version: str
            - title: str
            - shared_environment_spec: dict
            - scene_plans: list[dict] (恰好 2 个)
    """
    return {
        "template_id": "default_pet_destination",
        "template_version": "1.0",
        "title": "宠物温馨旅行小屋",
        "shared_environment_spec": {
            "description": "温馨舒适的室内环境，柔和的光线，木质家具",
            "style_constraints": ["温馨", "舒适", "自然光"],
            "composition_constraints": ["宠物在画面中心区域", "环境元素不遮挡宠物"],
            "negative_constraints": ["杂乱", "昏暗", "过度拥挤"],
        },
        "scene_plans": [
            {
                "order": 0,
                "state_label": "休息状态",
                "pet_behavior": "趴在地上休息",
                "pet_emotion": "放松平静",
                "semantic_anchor": "宠物趴在木地板上的温暖角落",
                "interaction_prompt": "轻轻抚摸休息中的宠物",
            },
            {
                "order": 1,
                "state_label": "活跃状态",
                "pet_behavior": "站立张望",
                "pet_emotion": "好奇兴奋",
                "semantic_anchor": "宠物站在窗边眺望外面",
                "interaction_prompt": "和宠物一起看窗外风景",
            },
        ],
    }


def mock_select_environment_template(
    requirements: dict[str, Any],
) -> dict[str, Any]:
    """Mock 选择环境模板（真实实现会调用 LLM）。

    Args:
        requirements: Requirements 数据，包含 wish_items

    Returns:
        dict: 包含
            - style_template_id: str
            - composition_template_id: str
            - rationale: str (选择推理)
    """
    # Mock 实现：选择默认模板
    return {
        "style_template_id": "style_001",  # 几何幻想
        "composition_template_id": "composition_002",  # 单主体居中地标式
        "rationale": "基于玩家愿望，选择几何幻想画风营造梦幻氛围，使用单主体居中构图突出宠物主体。",
    }



# ============================================================================
# Workflow Nodes
# ============================================================================


def classify_input_node(state: ClarificationSpecState) -> ClarificationSpecState:
    """节点：分类输入（T3 阶段已由 T2 完成，这里只是占位）。"""
    # T2 已经在 Run 处理时完成了分类，这里不需要重新分类
    return state


def extract_wish_items_node(
    state: ClarificationSpecState, repo: DestinationRepository
) -> ClarificationSpecState:
    """节点：提取愿望条目。"""
    # 从 Repository 读取澄清输入
    inputs = repo.list_clarification_inputs(state["session_id"])
    state["clarification_inputs"] = inputs

    # 提取愿望条目
    wish_items = mock_extract_wish_items(inputs)
    state["wish_items"] = wish_items

    return state


def evaluate_close_condition_node(
    state: ClarificationSpecState, repo: DestinationRepository
) -> ClarificationSpecState:
    """节点：评估关闭条件。"""
    # 检查澄清状态
    clarif_state = repo.get_clarification_state(state["session_id"])

    if clarif_state and clarif_state["clarification_closed"]:
        state["close_condition_met"] = True
    else:
        state["close_condition_met"] = False

    return state


def freeze_requirements_node(
    state: ClarificationSpecState, repo: DestinationRepository
) -> ClarificationSpecState:
    """节点：冻结 Requirements。

    不变量（issue #10 第 4.3 节）：
    1. 冻结后不可修改
    2. source_type=agent_inference 必须有 rationale
    3. 系统安全策略不写进普通 requirements
    4. 下游只引用 requirements_id 与 SHA-256
    """
    destination_id = state["destination_id"]
    wish_items = state["wish_items"]

    if not wish_items:
        state["error"] = "没有可用的愿望条目"
        return state

    # 准备 source_inputs
    source_inputs = [
        {
            "input_id": inp["input_id"],
            "raw_text": inp["raw_text"],
            "classification": inp["classification"],
        }
        for inp in state["clarification_inputs"]
    ]

    # 计算 SHA-256（包含所有 source_inputs 和 wish_items）
    # Bug 1.3 验证：SHA-256 用于内容寻址，只包含业务内容字段
    # 不包含 requirements_id（生成的 ID）和 destination_id（上下文引用）
    # 包含字段：source_inputs (input_id, raw_text, classification)
    #          items (normalized_statement, polarity, fulfillment, source_type, source_input_ids, rationale)
    requirements_data = {
        "source_inputs": source_inputs,
        "items": wish_items,
    }
    requirements_json = json.dumps(requirements_data, sort_keys=True, ensure_ascii=False)
    requirements_sha256 = hashlib.sha256(requirements_json.encode("utf-8")).hexdigest()

    # 创建 DestinationRequirements
    requirements = repo.create_destination_requirements(
        destination_id=destination_id,
        source_inputs=source_inputs,
        sha256=requirements_sha256,
    )

    requirements_id = requirements["requirements_id"]

    # 创建各个 RequirementItem
    for item in wish_items:
        # 校验不变量：agent_inference 必须有 rationale
        if item["source_type"] == "agent_inference" and not item.get("rationale"):
            state["error"] = "agent_inference 类型的条目必须有 rationale"
            return state

        repo.create_requirement_item(
            requirements_id=requirements_id,
            normalized_statement=item["normalized_statement"],
            polarity=item["polarity"],
            fulfillment=item["fulfillment"],
            source_type=item["source_type"],
            source_input_ids=item["source_input_ids"],
            rationale=item.get("rationale"),
        )

    state["requirements_id"] = requirements_id
    state["requirements_sha256"] = requirements_sha256

    # 更新 Destination phase
    repo.update_destination_phase(destination_id, "requirements")

    return state


def design_environment_template_node(
    state: ClarificationSpecState, repo: DestinationRepository
) -> ClarificationSpecState:
    """节点：设计环境模板（Issue #36 - 2.2）。

    调用 LLM 结合 requirements 选择画风和构图模板。
    """
    destination_id = state["destination_id"]
    requirements_id = state["requirements_id"]
    wish_items = state["wish_items"]

    if not requirements_id or not wish_items:
        state["error"] = "Requirements 未冻结或愿望条目为空"
        return state

    # 准备 requirements 数据供 LLM 选择
    requirements_data = {
        "requirements_id": requirements_id,
        "wish_items": wish_items,
    }

    # 调用 LLM 选择模板（Mock 实现）
    template_selection = mock_select_environment_template(requirements_data)

    # 保存模板选择结果到 State
    state["style_template_id"] = template_selection["style_template_id"]
    state["composition_template_id"] = template_selection["composition_template_id"]
    state["template_design_rationale"] = template_selection["rationale"]

    # 持久化到数据库（Issue #36 - 2.3）
    repo.create_environment_template_design(
        destination_id=destination_id,
        requirements_id=requirements_id,
        style_template_id=template_selection["style_template_id"],
        composition_template_id=template_selection["composition_template_id"],
        rationale=template_selection["rationale"],
    )

    return state


def generate_destination_spec_node(
    state: ClarificationSpecState, repo: DestinationRepository
) -> ClarificationSpecState:
    """节点：生成 DestinationSpec（Issue #36 - 2.4：包含 environment_design）。"""
    requirements_id = state["requirements_id"]
    requirements_sha256 = state["requirements_sha256"]
    style_template_id = state.get("style_template_id")
    composition_template_id = state.get("composition_template_id")

    if not requirements_id or not requirements_sha256:
        state["error"] = "Requirements 未冻结"
        return state

    # 生成 Spec 内容（使用模板选择结果）
    spec_data = mock_generate_destination_spec(requirements_id, requirements_sha256)

    # Issue #36 - 2.4: 添加 environment_design 字段
    # 包含模板选择结果和渲染后的 prompt
    environment_design = {
        "style_template_id": style_template_id,
        "composition_template_id": composition_template_id,
        "rendered_prompt": f"使用 {style_template_id} 画风和 {composition_template_id} 构图创建温馨宠物环境",
    }

    # 将 environment_design 添加到 shared_environment_spec
    shared_environment_spec = spec_data["shared_environment_spec"].copy()
    shared_environment_spec["environment_design"] = environment_design

    destination_id = state["destination_id"]

    # 计算 Spec SHA-256
    spec_content = {
        "template_id": spec_data["template_id"],
        "template_version": spec_data["template_version"],
        "requirements_id": requirements_id,
        "requirements_sha256": requirements_sha256,
        "title": spec_data["title"],
        "shared_environment_spec": shared_environment_spec,
        "scene_plans": spec_data["scene_plans"],
    }
    spec_json = json.dumps(spec_content, sort_keys=True, ensure_ascii=False)
    spec_sha256 = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()

    # 创建 DestinationSpec
    spec = repo.create_destination_spec(
        destination_id=destination_id,
        spec_version=1,  # 首阶段固定为 1
        template_id=spec_data["template_id"],
        template_version=spec_data["template_version"],
        requirements_id=requirements_id,
        requirements_sha256=requirements_sha256,
        title=spec_data["title"],
        shared_environment_spec=shared_environment_spec,  # Issue #36 - 2.4: 使用包含 environment_design 的版本
        sha256=spec_sha256,
    )

    state["spec_id"] = spec["spec_id"]
    state["spec_sha256"] = spec_sha256
    state["scene_plan_data"] = spec_data["scene_plans"]

    return state


def validate_and_lock_spec_node(
    state: ClarificationSpecState, repo: DestinationRepository
) -> ClarificationSpecState:
    """节点：验证并锁定 Spec。

    不变量（issue #10 第 4.4、4.5 节）：
    1. 锁定后不可变
    2. 必须恰好包含两个 ScenePlan
    3. 两个场景必须具有不同的宠物行为或状态
    """
    spec_id = state["spec_id"]
    destination_id = state["destination_id"]
    scene_plan_data = state.get("scene_plan_data")

    # 检查 scene_plan_data 是否存在
    if not scene_plan_data:
        state["error"] = "scene_plan_data 不存在，无法创建 ScenePlan"
        return state

    # 不变量：必须恰好 2 个 ScenePlan
    if len(scene_plan_data) != 2:
        state["error"] = f"Spec 必须恰好包含 2 个 ScenePlan，实际为 {len(scene_plan_data)}"
        return state

    # 不变量：两个场景的宠物行为或状态不能完全相同
    plan0 = scene_plan_data[0]
    plan1 = scene_plan_data[1]

    if (
        plan0["pet_behavior"] == plan1["pet_behavior"]
        and plan0["pet_emotion"] == plan1["pet_emotion"]
        and plan0["state_label"] == plan1["state_label"]
    ):
        state["error"] = "两个 ScenePlan 的宠物行为、情绪和状态标签不能完全相同"
        return state

    # 创建两个 ScenePlan
    scene_plan_ids = []

    for plan in scene_plan_data:
        scene_plan = repo.create_scene_plan(
            destination_id=destination_id,
            spec_id=spec_id,
            order_index=plan["order"],
            state_label=plan["state_label"],
            pet_behavior=plan["pet_behavior"],
            pet_emotion=plan["pet_emotion"],
            semantic_anchor=plan["semantic_anchor"],
            interaction_prompt=plan["interaction_prompt"],
        )
        scene_plan_ids.append(scene_plan["scene_id"])

    state["scene_plan_ids"] = scene_plan_ids

    # 更新 Destination phase
    repo.update_destination_phase(destination_id, "specification")

    return state


# ============================================================================
# Workflow Builder
# ============================================================================


def build_clarification_spec_workflow(
    repo: DestinationRepository,
) -> StateGraph:
    """构建澄清与规格生成工作流。

    Args:
        repo: DestinationRepository 实例

    Returns:
        StateGraph: 编译好的工作流图
    """
    workflow = StateGraph(ClarificationSpecState)

    # 添加节点（使用 lambda 注入 repo 依赖）
    workflow.add_node("classify_input", classify_input_node)
    workflow.add_node(
        "extract_wish_items",
        lambda state: extract_wish_items_node(state, repo),
    )
    workflow.add_node(
        "evaluate_close_condition",
        lambda state: evaluate_close_condition_node(state, repo),
    )
    workflow.add_node(
        "freeze_requirements",
        lambda state: freeze_requirements_node(state, repo),
    )
    # Issue #36 - 2.2: 新增模板设计节点
    workflow.add_node(
        "design_environment_template",
        lambda state: design_environment_template_node(state, repo),
    )
    workflow.add_node(
        "generate_destination_spec",
        lambda state: generate_destination_spec_node(state, repo),
    )
    workflow.add_node(
        "validate_and_lock_spec",
        lambda state: validate_and_lock_spec_node(state, repo),
    )

    # 设置入口点
    workflow.set_entry_point("classify_input")

    # 添加边
    workflow.add_edge("classify_input", "extract_wish_items")
    workflow.add_edge("extract_wish_items", "evaluate_close_condition")
    workflow.add_edge("evaluate_close_condition", "freeze_requirements")
    # Issue #36 - 2.2: 插入模板设计节点
    workflow.add_edge("freeze_requirements", "design_environment_template")
    workflow.add_edge("design_environment_template", "generate_destination_spec")
    workflow.add_edge("generate_destination_spec", "validate_and_lock_spec")
    workflow.add_edge("validate_and_lock_spec", END)

    return workflow.compile()


# ============================================================================
# 主入口函数
# ============================================================================


def run_clarification_spec_workflow(
    session_id: str,
    destination_id: str,
    repo: DestinationRepository,
) -> dict[str, Any]:
    """运行澄清与规格生成工作流。

    Args:
        session_id: 会话 ID
        destination_id: 目的地 ID
        repo: DestinationRepository 实例

    Returns:
        dict: 工作流最终状态，包含
            - requirements_id: str | None
            - requirements_sha256: str | None
            - spec_id: str | None
            - spec_sha256: str | None
            - scene_plan_ids: list[str] | None
            - error: str | None
    """
    # 构建工作流
    app = build_clarification_spec_workflow(repo)

    # 初始化状态
    initial_state: ClarificationSpecState = {
        "session_id": session_id,
        "destination_id": destination_id,
        "clarification_inputs": [],
        "close_condition_met": False,
        "requirements_id": None,
        "requirements_sha256": None,
        "wish_items": None,
        "spec_id": None,
        "spec_sha256": None,
        "scene_plan_data": None,
        "scene_plan_ids": None,
        "error": None,
    }

    # 运行工作流
    final_state = app.invoke(initial_state)

    return {
        "requirements_id": final_state.get("requirements_id"),
        "requirements_sha256": final_state.get("requirements_sha256"),
        "spec_id": final_state.get("spec_id"),
        "spec_sha256": final_state.get("spec_sha256"),
        "scene_plan_ids": final_state.get("scene_plan_ids"),
        "error": final_state.get("error"),
    }
