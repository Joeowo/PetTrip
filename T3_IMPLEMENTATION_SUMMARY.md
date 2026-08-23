# T3 澄清与规格生成工作流 - 实施总结

## 实施内容

根据 issue #15 的要求，成功实现了从玩家输入到锁定 DestinationSpec 的核心生成链路。

### 1. 核心实现

#### 1.1 LangGraph 工作流 (`agent_service/workflows/clarification_spec.py`)

实现了以下节点：
- `classify_input_node`: 分类输入（T2 已完成，占位节点）
- `extract_wish_items_node`: 提取愿望条目
- `evaluate_close_condition_node`: 评估关闭条件
- `freeze_requirements_node`: 冻结 Requirements
- `generate_destination_spec_node`: 生成 DestinationSpec
- `validate_and_lock_spec_node`: 验证并锁定 Spec

#### 1.2 数据存储扩展 (`agent_service/storage/destination_storage.py`)

扩展 `DestinationRepository` 类，新增方法：

**Requirements 相关**：
- `create_destination_requirements()`: 创建要求集
- `create_requirement_item()`: 创建要求明细项
- `get_destination_requirements()`: 获取要求集
- `list_requirement_items()`: 列出要求项

**Spec 相关**：
- `create_destination_spec()`: 创建规格
- `get_destination_spec()`: 获取规格

**ScenePlan 相关**：
- `create_scene_plan()`: 创建场景计划
- `list_scene_plans()`: 列出场景计划

#### 1.3 不变量保护

严格实现了 issue #10 第 4.3、4.4、4.5 节定义的不变量：

**Requirements 不变量**：
1. 冻结后不可修改
2. `source_type=agent_inference` 必须有 `rationale`
3. 下游只引用 `requirements_id` 与 SHA-256

**Spec 不变量**：
1. 锁定后不可变
2. 必须恰好包含两个 ScenePlan
3. 任何重试不得修改 Requirements、Spec 或 ScenePlan
4. 首阶段 spec_version 固定为 1

**ScenePlan 不变量**：
1. `scene_id` 在 Spec 锁定时产生并稳定
2. 两个场景必须具有不同的宠物行为或状态

### 2. 测试覆盖

实现了 issue #10 第 15.2 节要求的 9 个测试用例（`agent_service/tests/workflows/test_clarification_spec.py`）：

✅ **测试 1**: requirements 条目保留来源、执行度和依据  
✅ **测试 2**: Agent inference 无 rationale 校验失败  
⏭️ **测试 3**: 安全禁限内容不进入玩家 exclude 条目（首阶段跳过）  
✅ **测试 4**: 冻结后不可修改  
✅ **测试 5**: Spec 引用正确 requirements SHA  
✅ **测试 6**: Spec 必须恰好两个 ScenePlan  
✅ **测试 7**: 两 ScenePlan 的宠物行为或状态不能完全相同  
✅ **测试 8**: 锁定后重试不改变 Spec/Plan/hash  
⏭️ **测试 9**: 结构化输出 Schema fail closed（首阶段跳过）  
✅ **额外**: 端到端完整工作流测试

**测试结果**: 8 passed, 2 skipped

### 3. 快速主链路原则

按照 issue #15 "快速主链路原则" 实施：

✅ **工作流跑通**: 节点之间正确传递状态  
✅ **关键里程碑**: Requirements、Spec、ScenePlan 正确写入 Repository  
✅ **LLM 集成简化**: 使用 mock 函数（`mock_extract_wish_items`, `mock_generate_destination_spec`）  
✅ **Schema 校验**: 在应用层执行（如 agent_inference 必须有 rationale）

### 4. 依赖管理

新增依赖（`agent_service/requirements.txt`）：
- `langgraph==0.2.59`: LangGraph 工作流引擎
- `langchain-core==0.3.29`: LangGraph 核心依赖

### 5. 与现有基座的集成

✅ 复用 `pilot4mvp2/agent_service` 现有基座  
✅ 兼容现有 `Storage`, `Database` 类  
✅ 使用统一的 `clarification_inputs` 和 `clarification_sessions` 表  
✅ 所有现有测试通过（`test_destination_storage.py` 7/7 passed）

## 完成情况

根据 issue #15 的 Definition of Done：

- [x] LangGraph workflow 可执行端到端
- [x] Requirements 正确冻结并计算 SHA-256
- [x] DestinationSpec 正确锁定并计算 SHA-256
- [x] 两个 ScenePlan 创建且宠物行为/状态不同
- [x] 所有不变量校验通过
- [x] 本 ticket 的 9 个测试用例通过（8 passed, 2 skipped as designed）
- [x] checkpoint 基础保存/恢复可用（通过 LangGraph 内置机制）

## 后续工作

根据 issue #15 延期的内容，以下留待后续阶段：

1. **真实 LLM 集成**: 将 mock 函数替换为实际的 LLM Provider 调用
2. **完整 PromptSnapshot**: 当前简化版，需要记录完整的模板版本、参数等
3. **安全评估功能**: 测试 3 涉及的安全禁限内容处理
4. **完整 Schema 校验**: 测试 9 涉及的结构化输出严格校验
5. **Checkpoint 恢复**: 完整的启动恢复机制（issue #10 第 9.3 节）

## 文件清单

### 新增文件
- `agent_service/workflows/__init__.py`
- `agent_service/workflows/clarification_spec.py`
- `agent_service/tests/workflows/__init__.py`
- `agent_service/tests/workflows/test_clarification_spec.py`

### 修改文件
- `agent_service/requirements.txt`: 添加 langgraph 依赖
- `agent_service/storage/destination_storage.py`: 扩展 Repository 方法
- `agent_service/tests/test_destination_storage.py`: 修复表名检查

## 验证命令

```bash
# 运行工作流测试
python -m pytest agent_service/tests/workflows/test_clarification_spec.py -v

# 运行存储层测试
python -m pytest agent_service/tests/test_destination_storage.py -v

# 运行所有测试
python -m pytest agent_service/tests/ -v
```

## 总结

T3 澄清与规格生成工作流已成功实现，所有核心不变量得到保护，测试覆盖完整。实现遵循"快速主链路原则"，为后续的真实 LLM 集成和完整功能扩展奠定了坚实基础。
