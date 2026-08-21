# Issue #10 修正清单

## 修正目标

将 issue #10 从"混淆静态/视频边界"修正为"明确的静态闭环规格，预留视频扩展接口"。

## 核心修正项

### 🔴 P0 修正（必须修正）

#### 1. 明确静态闭环范围（新增）

**问题**：规格未说明为何采用静态图层模型而非协议 3.0 的视频模式。

**修正**：在 Problem Statement 后新增 "Scope Clarification" 章节：

```markdown
## Scope Clarification

**本规格实现首个静态场景闭环**，交付静态图层 PNG + 点击区域，用于验证从愿望澄清到 Unity 展示的完整业务流程。

**协议 3.0 的视频场景包（`video_scene_package`）延后实现**，但本规格在数据模型中预留扩展接口：

- `SceneArtifact` 当前实现静态字段（`layers`、`points_of_interest`）
- 未来扩展时新增 `video_scene_package` 字段，与静态字段互斥
- `DestinationManifest.scenes[].artifact_type` 标识交付类型（`"static"` | `"video"`）

**静态闭环与视频闭环的差异**：

| 维度 | 静态闭环（本规格） | 视频闭环（协议 3.0） |
|------|------------------|---------------------|
| 交付物 | 分层 PNG + 点击区域 | Live 视频 + 音频 + 交互热区 |
| 宠物表现 | 静态 Sprite，Unity 内切换 | 视频中真实运动 |
| 定位方式 | 图层坐标 | 语义锚点 → 黑圈 → CV → Mask |
| 共享环境 | Hero Frame + Clean Plate（内部中间产物） | Hero Frame + Clean Plate（内部中间产物） |

**Out of Scope（视频闭环专属）**：
- 语义锚点 → 黑圈定位图 → CV 提取 → 正式 Mask 的完整流程
- `pet_targets` 的 `actual_pet_bbox` 和 `interaction_hitbox`
- Live 视频素材的生成与交付
- 音频生成与同步

这些内容将在视频闭环规格（issue #XX）中详细定义。
```

---

#### 2. 标记 destination_requirements 四类分类为"待验证"

**问题**：issue #9 明确说"需要实际 Agent 运行来验证粒度是否合适"，但 issue #10 直接定义了详细 Schema。

**修正**：在"实现决策 3"的 `destination_requirements` 结构后补充：

```markdown
#### ⚠️ 验证状态

**四类分类的概念边界已通过 issue #9 逻辑原型验证**，但以下细节**待实际 Agent 运行验证**：

1. **映射规则稳定性**：LLM 能否稳定地将自然语言愿望分类到四类？
2. **source_type 枚举完备性**：`player_explicit`、`player_choice_from_agent`、`agent_inference` 是否覆盖所有来源？
3. **粒度合理性**：四类分类是否过细或过粗？是否需要合并 `suggested` 和 `freedom`？

**降级方案**：如果 Agent 运行验证发现四类分类不稳定，可降级为简化版本：

```typescript
{
  destination_id: "dest_xyz789",
  raw_wishes: ["我想去海边", "温暖的氛围"],  // 保留原始文本
  constraints: ["必须有灯塔", "不要车辆"],    // 单一约束列表
  created_at: "2026-08-21T10:30:00Z"
}
```

**实现建议**：先实现简化版本，通过 pilot 验证四类分类稳定性后再扩展为完整结构。
```

---

#### 3. 移除 Hero Frame 和 Clean Plate 的公开暴露

**问题**：Hero Frame 和 Clean Plate 是**生成中间产物**，不应作为 Unity 契约的公开字段。

**修正**：

1. 从 `DestinationManifest.shared_environment` 中删除 `hero_frame` 和 `clean_plate` 字段
2. 保留 `shared_environment.revision` 用于版本追溯
3. 在"实现决策 4"中补充说明：

```markdown
#### 共享环境的职责边界

**`shared_environment` 在静态闭环中的作用**：

- `revision` 字段追溯共享环境的版本（共建时递增）
- **不暴露** Hero Frame 和 Clean Plate 的 URL 或状态
- Hero Frame 和 Clean Plate 是 **Agent 内部的生成中间产物**，用于：
  - Hero Frame：人工审美批准的主视觉方案
  - Clean Plate：从 Hero Frame 提取的纯背景，作为所有场景的共享基底

Unity 客户端**无需**访问这些中间产物，只需通过 `SceneArtifact.layers` 获取最终渲染的分层 PNG。

**视频闭环的变化**：
- 视频模式下，Hero Frame 和 Clean Plate 的作用更加关键（协议 3.0 定义）
- 但它们仍是内部产物，Unity 只需获取最终的 `video_scene_package`
```

修正后的 `DestinationManifest.shared_environment`：

```json
{
  "shared_environment": {
    "revision": 1,
    "updated_at": "2026-08-21T10:34:00Z"
  }
}
```

---

#### 4. 明确坐标系统定义

**问题**：规格中使用了 `position: {x, y}` 但未定义坐标系统语义。

**修正**：在"实现决策 5"的 `SceneArtifact` 结构前新增：

```markdown
#### 坐标系统定义

**静态闭环采用 Unity 标准坐标系**：

- **原点**：画布左下角 (0, 0)
- **单位**：像素（整数）
- **X 轴**：向右递增
- **Y 轴**：向上递增
- **画布尺寸**：由 `canvas.width` 和 `canvas.height` 定义（如 512×288）

示例：
```json
{
  "canvas": {"width": 512, "height": 288},
  "layers": [
    {
      "position": {"x": 256, "y": 144}  // 画布中心
    }
  ]
}
```

**与协议 3.0 的差异**：
- 协议 3.0 使用 `normalized_top_left` 归一化坐标（[0,0] 到 [1,1]）
- 静态闭环使用像素坐标，更符合 Unity Sprite 渲染习惯
- 视频闭环规格将恢复使用归一化坐标
```

---

#### 5. 补充澄清封盘的状态机逻辑

**问题**：规格定义了 `ClarificationSession` 结构，但 `accepted_wishes` 和 `unaccepted_inputs` 的累积规则未明确。

**修正**：在"实现决策 3"的澄清会话结构后补充：

```markdown
#### 澄清封盘状态机

**封盘条件**（满足任一即触发）：
1. Unity 主动调用 `/api/clarifications/{session_id}/close`
2. 已接受愿望达到 3 个（`total_accepted_wishes >= 3`）
3. 累计未接受输入达到 5 次（`total_unaccepted_inputs >= 5`）

**轮次与累积规则**：

| 字段 | 作用域 | 累积规则 |
|------|--------|---------|
| `rounds[].accepted_wishes` | 单轮 | 本轮新接受的愿望列表 |
| `rounds[].unaccepted_inputs` | 单轮 | 本轮未接受的输入列表 |
| `total_accepted_wishes` | 会话级 | 所有轮次的 `accepted_wishes` 累加计数 |
| `total_unaccepted_inputs` | 会话级 | 所有轮次的 `unaccepted_inputs` 累加计数 |

**空输入处理**（基于 issue #8）：
- `player_input.is_empty: true` 时，不推进轮次
- `agent_response.text` 为引导性提示（如"请描述你想去的地方"）
- `accepted_count` 保持为 0，不影响累积计数

**封盘后的不可变性**：
- `is_closed: true` 后，拒绝新的 `/inputs` 请求（返回 400 错误）
- `destination_requirements` 生成后不可修改
- `destination_id` 在封盘时创建，作为后续轮询的稳定标识

**伪代码示例**：

```python
def should_close_clarification(session: ClarificationSession) -> tuple[bool, str | None]:
    if session.is_closed:
        return True, session.closed_by
    
    if session.total_accepted_wishes >= 3:
        return True, "third_round"
    
    if session.total_unaccepted_inputs >= 5:
        return True, "five_unaccepted"
    
    return False, None
```
```

---

### 🟡 P1 修正（强烈建议）

#### 6. 统一 API 端点风格为 Run 模型

**问题**：规格使用 REST 资源风格（`/api/clarifications`、`/api/destinations`），与现有 `pilot4mvp2/app.py` 的 Run 中心模型不一致。

**修正**：在"API 契约定义"章节中补充说明：

```markdown
#### API 设计原则

**与现有架构对齐**：
- 现有 `pilot4mvp2/agent_service/app.py` 使用 **Run 中心模型**：所有异步操作都是 Run
- 澄清会话和目的地生成应纳入同一 Run 模型，而非创建新的资源端点

**替代方案（与现有架构一致）**：

1. **澄清会话 = 特殊类型的 Run**：
   ```
   POST /api/v1/runs
   {
     "session_id": "sess_abc",
     "input": {"text": "我想去海边"},
     "response_format": {"type": "clarification"}
   }
   
   GET /api/v1/runs/{run_id}
   响应：{status: "completed", output: {accepted: true, round_number: 1}}
   ```

2. **目的地生成 = 另一种 Run**：
   ```
   POST /api/v1/runs
   {
     "session_id": "sess_abc",
     "input": {"clarification_result": {...}},
     "response_format": {"type": "destination"}
   }
   ```

**本规格采用独立端点的原因**：
- 澄清会话需要**多轮交互**，不适合单次 Run 模型
- 目的地生成是**长时运行任务**，状态轮询更适合独立端点

**实现建议**：
- 如果 Unity 团队更倾向于统一的 Run API，可将本规格的端点重构为 Run 模型
- 保持灵活性：独立端点和 Run 模型可共存（独立端点内部调用 Run）
```

---

#### 7. 明确 Schema 组织方式

**问题**：规格提出创建独立的 JSON Schema 文件，但现有架构使用 Pydantic 为主。

**修正**：在"Schema 文件组织"章节补充：

```markdown
#### Schema 来源与导出

**主要 Schema 定义**：
- Python Pydantic 模型（`agent_service/schemas.py`）
- 使用 `ConfigDict(extra="forbid")` 禁止额外字段
- 通过 FastAPI 自动生成 OpenAPI Schema

**JSON Schema 文件（供 Unity 独立验证）**：
- 从 Pydantic 模型导出：`model.model_json_schema()`
- 存放在 `contracts/*/v1.0.schema.json`
- **不手工维护**，通过脚本自动生成

**生成脚本示例**：

```python
# scripts/export_schemas.py
from agent_service.schemas import DestinationSpec, ScenePlan, SceneArtifact
import json
from pathlib import Path

def export_schema(model_class, output_path: Path):
    schema = model_class.model_json_schema()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False))

export_schema(DestinationSpec, Path("contracts/destination-spec/v1.0.schema.json"))
export_schema(ScenePlan, Path("contracts/scene-plan/v1.0.schema.json"))
export_schema(SceneArtifact, Path("contracts/scene-artifact/v1.0.schema.json"))
```

**参考现有模式**：
- `pilot4mvp/session4/contracts/scene-snapshot-v0.2.schema.json` 是独立验证用
- 开发时的主要 Schema 来源是 `models.py`，不是 JSON 文件
```

---

#### 8. 具体化测试策略

**问题**：测试决策过于抽象，缺少具体测试用例清单。

**修正**：在"测试决策"的每个模块中补充至少 5 个具体测试用例：

```markdown
#### 1. Schema 验证测试（`tests/test_schemas.py`）

**具体测试用例**：

1. `test_destination_spec_requires_all_mandatory_fields()`
   - 缺少 `destination_id` → ValidationError
   - 缺少 `title` → ValidationError
   - 缺少 `spec_version` → ValidationError

2. `test_scene_artifact_revision_must_be_positive()`
   - `revision: 0` → ValidationError（最小值为 1）
   - `revision: -1` → ValidationError

3. `test_scene_plan_pet_state_must_be_valid_enum()`
   - `pet_state: "invalid"` → ValidationError
   - `pet_state: "idle"` → 通过

4. `test_extra_fields_are_forbidden()`
   - `DestinationSpec(destination_id="d1", unknown_field="x")` → ValidationError

5. `test_nested_validation_cascades()`
   - `SceneArtifact` 的 `layers[].asset_id` 缺失 → ValidationError

---

#### 2. API 契约测试（`tests/test_api_contract.py`）

**具体测试用例**：

1. `test_create_clarification_returns_201_with_session_id()`
   - POST `/api/clarifications` → 201 Created
   - 响应包含 `session_id` 和 `status: "active"`

2. `test_submit_input_to_nonexistent_session_returns_404()`
   - POST `/api/clarifications/nonexistent/inputs` → 404 Not Found

3. `test_get_destination_manifest_when_scenes_generating()`
   - GET `/api/destinations/{id}` → 200 OK
   - `scenes[0].status: "ready"`，`scenes[1].status: "generating"`

4. `test_close_already_closed_clarification_returns_400()`
   - POST `/api/clarifications/{closed_session_id}/close` → 400 Bad Request

5. `test_get_scene_artifact_returns_complete_structure()`
   - GET `/api/destinations/{id}/scenes/{scene_id}/artifact` → 200 OK
   - 响应包含 `canvas`、`layers`、`points_of_interest`、`assets`

---

#### 3. 状态机转换测试（`tests/test_state_transitions.py`）

**具体测试用例**（基于 issue #9 原型的 5 个引导场景）：

1. `test_normal_flow_clarify_to_ready_to_done()`
   - 提交 3 轮输入 → `is_closed: true`
   - 轮询 manifest → `status: "spec_locked"`
   - 等待场景生成 → `scenes[].status: "ready"`
   - Agent 发送 done → `status: "done"`

2. `test_third_round_auto_closes_clarification()`
   - 提交第 3 个已接受愿望 → 自动封盘
   - `closed_by: "third_round"`

3. `test_five_unaccepted_inputs_auto_closes()`
   - 提交 5 次未接受输入 → 自动封盘
   - `closed_by: "five_unaccepted"`

4. `test_scene_failure_retries_up_to_3_attempts()`
   - 场景生成失败 → `attempt: 1`，`status: "generating"`
   - 再次失败 → `attempt: 2`
   - 第 3 次失败 → `attempt: 3`，`status: "failed"`，`retryable: false`

5. `test_co_building_atomic_switch_from_staging_to_published()`
   - 共建开始 → `revision: 2`
   - 所有场景 `ready` → 原子切换 `revision: 3`
   - `content_version` 递增

---

#### 4. 失败处理测试（`tests/test_failure_scenarios.py`）

**具体测试用例**：

1. `test_scene_timeout_triggers_retry_with_attempt_increment()`
2. `test_all_scenes_failed_returns_fallback_destination_id()`
3. `test_non_scene_step_failure_records_attempt_and_retryable()`
4. `test_retryable_false_stops_further_attempts()`
5. `test_error_code_is_stable_enum_value()`
```

---

### 🟢 P2 修正（改进建议）

#### 9. 补充多角色用户故事

在"User Stories"章节开头补充：

```markdown
### 角色说明

本规格涉及以下角色：

- **Unity 客户端**：调用 Agent API 的前端系统（主要角色）
- **Agent 服务**：生成目的地和场景的后端系统
- **玩家**：最终用户，通过 Unity 客户端与 Agent 交互
- **审核者**：人工审核 Hero Frame 和共建版本的角色（静态闭环中暂不涉及）

大部分用户故事从"Unity 客户端"视角编写，但以下故事涉及其他角色：

- 作为 Agent 服务，我希望记录每个场景的 `attempt` 和操作日志，以便追溯失败原因
- 作为玩家，我希望澄清阶段看到"已采纳 X 个愿望"的反馈，以便知道我的输入是否被理解
```

---

#### 10. 澄清版本追溯三层模型的语义

在"实现决策 2"中补充真值表：

```markdown
#### 版本递增时机真值表

| 事件 | spec_version | content_version | revision | 说明 |
|------|--------------|-----------------|----------|------|
| 初次创建目的地 | "1.0" | 1 | 1 | 基线版本 |
| 共建修改标题 | "1.0" | 2 | 1 | 内容变化，环境不变 |
| 共建修改场景设计 | "1.0" | 2 | 1 → 2（暂存） | 开始重新生成场景 |
| 共建场景全部 ready | "1.0" | 2 | 2 → 3（发布） | 原子切换到正式版本 |
| 模板新增约束类型 | "1.1" | 1 | 1 | 框架变化，重置内容版本 |

**关键规则**：
- `spec_version` 变化时，`content_version` 和 `revision` 重置为 1
- `content_version` 递增时，`revision` 可能保持不变（仅修改标题）或递增（修改场景设计需要重新生成共享环境）
- `revision` 的奇数版本是正式版本，偶数版本是暂存版本
```

---

## 修正后的文档结构

修正后的 issue #10 应包含以下章节：

1. **Problem Statement**（保持不变）
2. **Scope Clarification**（新增）- 明确静态闭环范围
3. **Solution**（保持不变）
4. **User Stories**（补充多角色说明）
5. **Implementation Decisions**（所有 P0/P1/P2 修正）
   - 每个决策补充"验证状态"或"与协议 3.0 的关系"
6. **Testing Decisions**（具体化测试用例）
7. **Out of Scope**（补充"静态闭环专属延期项"）
8. **Further Notes**（补充"待决策项的处理状态"）

## 下一步行动

1. ✅ 本文档记录了所有修正项
2. ⏭️ 基于本清单重写 issue #10 的完整规格
3. ⏭️ 重新打开 issue #10，替换为修正后的内容
4. ⏭️ 更新 map #1 的 Decisions so far，注明 issue #10 已修正
5. ⏭️ 为 issue #9 的 6 个待决策项创建新 wayfinder tickets（如有必要）
