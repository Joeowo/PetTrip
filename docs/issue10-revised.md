# 统一目的地数据模型与 Unity 交付契约规格（静态闭环）

## Scope Clarification

**本规格实现首个静态场景闭环**，交付静态图层 PNG + 点击区域，用于验证从愿望澄清到 Unity 展示的完整业务流程。

**协议 3.0 的视频场景包（`video_scene_package`）延后实现**，但本规格在数据模型中预留扩展接口：

- `SceneArtifact` 当前实现静态字段（`layers`、`points_of_interest`）
- 未来扩展时新增 `video` 字段，与静态字段互斥
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

这些内容将在视频闭环规格中另行定义。

---

## Problem Statement

当前 PetTrip Agent-Unity 交互协议文档已形成共识，但缺少可直接用于实现的正式数据契约定义。具体问题包括：

1. **边界模糊**：目的地规格、场景计划、场景产物三个核心对象的字段边界在协议文档中以自然语言描述，缺少可验证的 Schema 定义
2. **版本追溯不明确**：规格版本、内容版本、环境版本三层模型的具体字段定义和递增规则未形式化
3. **状态转换隐含**：从愿望澄清、规格锁定、场景生成到共建发布的状态机转换规则散落在协议文档各处，缺少统一视图
4. **API 契约缺失**：Unity 轮询获取目的地、场景和共建版本的 API 端点、请求/响应格式未标准化
5. **失败处理不完整**：场景失败重试、非场景步骤失败的契约表达和 Unity 侧感知方式未明确

这导致实现团队无法直接从协议文档进入编码，需要在开发过程中反复对齐字段命名、嵌套结构和失败语义。

---

## Solution

定义一套统一的 JSON Schema 和 RESTful API 契约，涵盖从愿望澄清到静态场景交付的完整生命周期，使得：

1. **清晰的对象边界**：通过 `DestinationSpec`、`ScenePlan`、`SceneArtifact` 三个根对象明确职责分离
2. **可追溯的版本模型**：使用 `spec_version`（模板变化）、`content_version`（共建递增）、`revision`（环境版本）三层字段记录变更历史
3. **显式的状态机**：通过 `DestinationManifest` 聚合所有场景状态，Unity 通过单次轮询即可获取完整目的地状态
4. **标准化的 API**：定义 `/api/destinations/{destination_id}`、`/api/destinations/{destination_id}/scenes/{scene_id}` 等端点及其响应格式
5. **结构化的失败信息**：场景失败包含 `attempt`、`error_code`、`retryable` 字段，Unity 可区分临时失败与终止失败
6. **视频扩展预留**：`SceneArtifact` 结构支持未来新增 `video` 字段，无需重构现有静态字段

从用户（Unity 客户端）视角，交付契约提供"一次轮询获取全部状态，逐场景按需拉取产物"的简洁交互模式。

---

## User Stories

### 角色说明

本规格涉及以下角色：

- **Unity 客户端**：调用 Agent API 的前端系统（主要角色）
- **Agent 服务**：生成目的地和场景的后端系统
- **玩家**：最终用户，通过 Unity 客户端与 Agent 交互
- **审核者**：人工审核 Hero Frame 和共建版本的角色（静态闭环中暂不涉及）

### 愿望澄清阶段

1. 作为 Unity 客户端，我希望发起澄清会话时获得稳定的 `session_id`，以便后续提交玩家输入时引用同一会话
2. 作为 Unity 客户端，我希望提交玩家文本输入后得到明确的接受/拒绝反馈，以便向玩家展示"已采纳"或"需重新表达"
3. 作为 Unity 客户端，我希望通过单个端点查询澄清会话状态，获取当前轮次、已接受愿望数、未接受输入数和是否已封盘，以便决定是否继续收集输入
4. 作为 Unity 客户端，我希望主动结束澄清会话时，Agent 将当前进度封盘并生成 `destination_requirements`，即使未达到三轮或五次拒绝
5. 作为 Unity 客户端，我希望澄清封盘后自动创建 `destination_id`，以便立即开始轮询目的地状态而无需等待规格锁定
6. 作为玩家，我希望澄清阶段看到"已采纳 X 个愿望"的反馈，以便知道我的输入是否被理解

### 目的地规格阶段

7. 作为 Unity 客户端，我希望通过 `destination_id` 查询 `DestinationSpec`，获取标题、设定、画风和约束条件，以便在生成前向玩家展示即将创建的目的地概念
8. 作为 Unity 客户端，我希望 `DestinationSpec` 包含 `spec_version` 字段，当模板结构变化时能识别不兼容版本
9. 作为 Unity 客户端，我希望 `DestinationSpec` 一旦锁定（`locked: true`）即不可变，以便安全地缓存并作为后续场景的稳定基线
10. 作为 Unity 客户端，我希望 `destination_requirements` 保留原始玩家表述和分类标签（required/suggested/freedom/forbidden），以便追溯每个设计决策的来源

### 场景计划与生成阶段

11. 作为 Unity 客户端，我希望通过 `DestinationManifest` 获取所有 `ScenePlan` 的 `scene_id` 列表，以便知道本目的地包含哪些场景
12. 作为 Unity 客户端，我希望 `ScenePlan` 描述宠物状态、期望行为和视觉要素，但不包含实际生成产物，以便在生成前理解场景意图
13. 作为 Unity 客户端，我希望 `ScenePlan` 包含 `content_version` 字段，当共建修改场景设计时能识别变更
14. 作为 Unity 客户端，我希望场景生成开始后，`DestinationManifest.scenes[].status` 从 `pending` 变为 `generating`，以便向玩家展示进度
15. 作为 Unity 客户端，我希望场景生成失败时，`status` 变为 `failed` 并包含 `attempt`、`error_code` 和 `retryable` 字段，以便决定是否向玩家展示"正在重试"
16. 作为 Unity 客户端，我希望场景失败最多重试 3 次（`attempt` 从 1 到 3），超过后标记为终止失败，以避免无限等待
17. 作为 Agent 服务，我希望记录每个场景的 `attempt` 和操作日志，以便追溯失败原因

### 场景产物与资源交付

18. 作为 Unity 客户端，我希望场景生成成功后，`DestinationManifest.scenes[].status` 变为 `ready`，并提供 `artifact_url`，以便立即拉取该场景产物
19. 作为 Unity 客户端，我希望通过 `artifact_url` 获取 `SceneArtifact`，包含图层信息、兴趣点坐标、资源 SHA256 和画布尺寸，以便渲染静态场景
20. 作为 Unity 客户端，我希望场景 1 `ready` 后立即拉取，无需等待场景 2，以便缩短玩家感知延迟
21. 作为 Unity 客户端，我希望 `SceneArtifact` 包含 `revision` 字段，区分共享环境的不同版本（如 `staging` 与 `published`）
22. 作为 Unity 客户端，我希望资源 URI 使用稳定的内容寻址（如 SHA256 前缀），以便安全地缓存图片资源
23. 作为 Unity 客户端，我希望 `SceneArtifact.layers` 明确每个图层的 `sorting_order` 和 `position`，以便按正确 Z 轴顺序渲染

### 共享环境与共建

24. 作为 Unity 客户端，我希望 `DestinationManifest` 包含 `shared_environment.revision` 标识当前环境版本（如 `1` 表示初版，`2` 表示共建暂存），以便在共建流程中追踪变化
25. 作为 Unity 客户端，我希望共建过程中，Agent 在 `revision: 2` 上暂存所有场景，仅当全部 `ready` 后才原子切换到 `published` 状态
26. 作为 Unity 客户端，我希望轮询时如果检测到 `revision` 递增，能识别出需要重新拉取所有场景产物

### 目的地清单与完成信号

27. 作为 Unity 客户端，我希望 `DestinationManifest.status` 标识整体状态（`clarifying`/`spec_locked`/`scenes_generating`/`ready`/`done`/`failed`），以便决定 UI 展示
28. 作为 Unity 客户端，我希望所有计划场景均为 `ready` 时，`status` 变为 `ready`，表示技术交付完成
29. 作为 Unity 客户端，我希望 Agent 发送 `done` 事件后，`status` 变为 `done`，表示本轮业务终止且无更多场景
30. 作为 Unity 客户端，我希望如果目的地生成终止失败（所有场景均失败或非场景步骤失败），`status` 变为 `failed` 并提供 `error` 对象

### 失败处理与降级

31. 作为 Unity 客户端，我希望场景失败时 `retryable: true` 表示 Agent 将自动重试，`retryable: false` 表示终止失败
32. 作为 Unity 客户端，我希望非场景步骤失败（如规格生成、Hero Frame 审批）也记录 `attempt` 和 `retryable`，以便统一处理重试逻辑
33. 作为 Unity 客户端，我希望目的地完全失败时，`DestinationManifest.fallback_destination_id` 提供预置目的地 ID，以便无缝切换到兜底内容
34. 作为 Unity 客户端，我希望失败的 `error_code` 采用稳定枚举值（如 `CONTENT_SAFETY_REJECTED`、`GENERATION_TIMEOUT`），以便本地化错误消息

### 数据一致性与幂等性

35. 作为 Unity 客户端，我希望通过 `destination_id` 和 `scene_id` 的稳定标识，重复轮询同一资源时得到幂等响应
36. 作为 Unity 客户端，我希望 `DestinationManifest` 包含 `updated_at` 时间戳，以便实现基于时间的条件轮询（如 If-Modified-Since）
37. 作为 Unity 客户端，我希望 `SceneArtifact` 中的 `asset_id` 与 `sha256` 一一对应，以便验证下载完整性
38. 作为 Unity 客户端，我希望 API 返回 `ETag` 响应头，支持 304 Not Modified 缓存策略

### API 错误处理

39. 作为 Unity 客户端，我希望 API 返回标准化的错误响应格式（`error.code`、`error.message`、`error.retryable`），以便统一错误处理逻辑
40. 作为 Unity 客户端，我希望 404 表示资源不存在（如无效 `destination_id`），400 表示请求格式错误，503 表示临时不可用
41. 作为 Unity 客户端，我希望 `retryable: true` 的错误包含 `retry_after` 秒数建议，以避免过于激进的重试

### 扩展性与向后兼容

42. 作为 Unity 客户端，我希望 Schema 包含 `schema_version` 字段，当 Agent 升级契约时能识别不兼容变更
43. 作为 Unity 客户端，我希望新增的可选字段不影响现有解析逻辑（向后兼容的扩展）
44. 作为 Unity 客户端，我希望未来支持视频场景时，`SceneArtifact` 可扩展 `video` 字段而无需重构整体结构

---

## Implementation Decisions

### 核心数据模型

#### 1. 三对象边界分离

基于 issue #3 和 #9 的决策，定义三个根对象明确职责：

- **`DestinationSpec`**（目的地规格）：锁定的设计基线，包含 `destination_id`、`spec_version`、`content_version`、`title`、`setting`、`visual_profile`、`constraints`、`locked`
- **`ScenePlan`**（场景计划）：单个场景的意图描述，包含 `scene_id`、`destination_id`、`content_version`、`pet_state`、`expected_behavior`、`visual_elements`
- **`SceneArtifact`**（场景产物）：单个场景的实际生成结果，包含 `scene_id`、`destination_id`、`revision`、`canvas`、`layers`、`points_of_interest`、`assets`

三者通过 `destination_id` 关联，`ScenePlan` 描述"要什么"，`SceneArtifact` 提供"生成了什么"，`DestinationSpec` 作为全局基线约束两者。

**静态闭环的特化**：
- `SceneArtifact.layers` 包含分层 PNG 的图层信息（`asset_id`、`sorting_order`、`position`）
- `SceneArtifact.points_of_interest` 定义点击区域和交互类型
- 未来视频闭环时，新增 `SceneArtifact.video` 字段（与 `layers` 互斥）

---

#### 2. 版本追溯三层模型

基于 issue #9 原型验证的版本模型：

```typescript
{
  spec_version: "1.0",        // 模板结构变化时递增（如新增约束类型）
  content_version: 1,         // 共建修改内容时递增（如改标题、调整场景设计）
  revision: 1                 // 共享环境版本（1=初版，2=共建暂存，发布后继续递增）
}
```

- `spec_version`：字符串，语义化版本（如 "1.0"、"1.1"），模板框架变化时递增，不同 `spec_version` 的 Schema 可能不兼容
- `content_version`：整数，从 1 开始，共建修改目的地内容时递增，同一 `spec_version` 下的内容迭代
- `revision`：整数，从 1 开始，共享环境（Hero Frame + Clean Plate）的版本号，共建暂存时递增到 2，发布后变为正式版本

**版本递增时机真值表**：

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

---

#### 3. 澄清会话与 destination_requirements

基于 issue #2 和 #8 的决策，定义澄清对象：

```typescript
// 澄清会话状态
{
  session_id: "clarif_abc123",
  rounds: [
    {
      round_number: 1,
      player_input: {text: "我想去海边", is_empty: false},
      agent_response: {text: "...", accepted_count: 1},
      accepted_wishes: ["地点：海边"],
      unaccepted_inputs: []
    }
  ],
  is_closed: false,
  closed_by: null,  // "unity_signal" | "third_round" | "five_unaccepted"
  total_accepted_wishes: 1,
  total_unaccepted_inputs: 0
}
```

**澄清封盘状态机**：

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

**destination_requirements 四类分类**：

```typescript
{
  destination_id: "dest_xyz789",
  categories: {
    required: [
      {text: "海边环境", source_round: 1, source_type: "player_explicit"}
    ],
    suggested: [
      {text: "温暖氛围", source_round: 2, source_type: "player_choice_from_agent"}
    ],
    freedom: [
      {text: "宠物具体动作", source_type: "agent_inference"}
    ],
    forbidden: [
      {text: "暴力内容", source_round: 3, source_type: "player_explicit"}
    ]
  },
  created_at: "2026-08-21T10:30:00Z"
}
```

四类分类的语义（来自 issue #9 原型验证）：
- `required`：玩家明确要求，必须满足
- `suggested`：玩家接受的 Agent 推荐或模糊表达，尽量满足
- `freedom`：Agent 创作自由度范围，无强制约束
- `forbidden`：玩家明确排除，必须避免

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

---

#### 4. DestinationManifest 聚合对象

Unity 通过单次查询 `/api/destinations/{destination_id}` 获取完整状态：

```json
{
  "destination_id": "dest_xyz789",
  "status": "scenes_generating",
  "destination_spec": {
    "destination_id": "dest_xyz789",
    "spec_version": "1.0",
    "content_version": 1,
    "title": "宁静海岸",
    "setting": "黄昏时分的海边灯塔",
    "visual_profile": {
      "art_style": "pixel_art",
      "color_palette": "warm_sunset",
      "resolution": {"width": 512, "height": 288}
    },
    "constraints": {
      "forbidden_objects": ["vehicle"],
      "required_elements": ["lighthouse", "pet"]
    },
    "locked": true,
    "locked_at": "2026-08-21T10:35:00Z"
  },
  "scenes": [
    {
      "scene_id": "scene_001",
      "status": "ready",
      "artifact_type": "static",
      "content_version": 1,
      "artifact_url": "/api/destinations/dest_xyz789/scenes/scene_001/artifact",
      "plan_summary": "宠物在海边挥手"
    },
    {
      "scene_id": "scene_002",
      "status": "generating",
      "artifact_type": "static",
      "content_version": 1,
      "attempt": 1,
      "artifact_url": null
    }
  ],
  "shared_environment": {
    "revision": 1,
    "updated_at": "2026-08-21T10:34:00Z"
  },
  "updated_at": "2026-08-21T10:36:00Z"
}
```

**共享环境的职责边界**：

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

---

#### 5. SceneArtifact 结构与坐标系统

**坐标系统定义**：

**静态闭环采用 Unity 标准坐标系**：

- **原点**：画布左下角 (0, 0)
- **单位**：像素（整数）
- **X 轴**：向右递增
- **Y 轴**：向上递增
- **画布尺寸**：由 `canvas.width` 和 `canvas.height` 定义（如 512×288）

**与协议 3.0 的差异**：
- 协议 3.0 使用 `normalized_top_left` 归一化坐标（[0,0] 到 [1,1]）
- 静态闭环使用像素坐标，更符合 Unity Sprite 渲染习惯
- 视频闭环规格将恢复使用归一化坐标

**SceneArtifact 结构**：

```json
{
  "scene_id": "scene_001",
  "destination_id": "dest_xyz789",
  "revision": 1,
  "content_version": 1,
  "artifact_type": "static",
  "canvas": {
    "width": 512,
    "height": 288,
    "pixels_per_unit": 16
  },
  "layers": [
    {
      "layer_id": "background",
      "asset_id": "asset_bg_001",
      "sorting_order": 0,
      "position": {"x": 0, "y": 0}
    },
    {
      "layer_id": "pet",
      "asset_id": "asset_pet_001",
      "sorting_order": 10,
      "position": {"x": 256, "y": 144}
    }
  ],
  "points_of_interest": [
    {
      "poi_id": "pet_interaction",
      "kind": "pet_wave",
      "click_region": {
        "type": "circle",
        "center": {"x": 256, "y": 144},
        "radius": 32
      },
      "description": "点击宠物挥手"
    }
  ],
  "assets": [
    {
      "asset_id": "asset_bg_001",
      "kind": "sprite",
      "filename": "background.png",
      "uri": "/api/assets/sha256_abc123.png",
      "mime_type": "image/png",
      "width": 512,
      "height": 288,
      "channels": 4,
      "anchor": {"x": 0.5, "y": 0.5},
      "sha256": "abc123..."
    }
  ],
  "generated_at": "2026-08-21T10:35:30Z"
}
```

**视频扩展预留**：

未来视频闭环时，`SceneArtifact` 新增 `video` 字段（与 `layers` 互斥）：

```json
{
  "scene_id": "scene_001",
  "artifact_type": "video",
  "video": {
    "uri": "/api/assets/sha256_video.mp4",
    "duration_ms": 5000,
    "pet_targets": [
      {
        "actual_pet_bbox": {"x": 0.4, "y": 0.5, "w": 0.15, "h": 0.2},
        "interaction_hitbox": {"x": 0.38, "y": 0.48, "w": 0.19, "h": 0.24}
      }
    ]
  }
}
```

---

#### 6. 失败处理与重试

基于 issue #7 的决策，场景失败最多 3 次 attempt：

```json
{
  "scene_id": "scene_002",
  "status": "failed",
  "attempt": 3,
  "error": {
    "code": "GENERATION_TIMEOUT",
    "message": "场景生成超时",
    "retryable": false,
    "details": {
      "last_attempt_at": "2026-08-21T10:40:00Z"
    }
  }
}
```

目的地级别失败包含 fallback：

```json
{
  "destination_id": "dest_xyz789",
  "status": "failed",
  "error": {
    "code": "ALL_SCENES_FAILED",
    "message": "所有场景生成失败"
  },
  "fallback_destination_id": "dest_preset_default_001"
}
```

---

#### 7. 共建原子切换

基于 issue #9 原型场景 5 验证的逻辑：

1. 共建开始时，创建 `revision: 2` 的暂存环境
2. 在 `revision: 2` 上重新生成所有场景
3. 仅当所有场景均为 `ready` 时，原子切换：
   - `shared_environment.revision` 从 2 变为 3（发布版本）
   - 所有 `SceneArtifact.revision` 从 2 更新为 3
   - `DestinationSpec.content_version` 递增
4. Unity 检测到 `revision` 变化后，重新拉取所有场景

---

#### 8. 字段命名约定

统一使用 `snake_case`：
- 所有 JSON 字段：`destination_id`、`scene_id`、`content_version`
- 所有 API 路径：`/api/destinations/{destination_id}/scenes/{scene_id}`
- 枚举值使用小写下划线：`"pet_wave"`、`"content_safety_rejected"`

Python Pydantic 模型与 JSON Schema 保持一致命名。

---

### API 契约定义

#### API 设计原则

**与现有架构对齐**：
- 现有 `pilot4mvp2/agent_service/app.py` 使用 **Run 中心模型**：所有异步操作都是 Run
- 澄清会话和目的地生成应考虑纳入同一 Run 模型，而非创建新的资源端点

**本规格采用独立端点的原因**：
- 澄清会话需要**多轮交互**，不适合单次 Run 模型
- 目的地生成是**长时运行任务**，状态轮询更适合独立端点

**实现建议**：
- 如果 Unity 团队更倾向于统一的 Run API，可将本规格的端点重构为 Run 模型
- 保持灵活性：独立端点和 Run 模型可共存（独立端点内部调用 Run）

#### 核心端点

1. **POST /api/clarifications** - 创建澄清会话
   - 请求体：`{player_context?: object}`
   - 响应：`{session_id: string, status: "active"}`

2. **POST /api/clarifications/{session_id}/inputs** - 提交玩家输入
   - 请求体：`{text: string, is_empty: boolean}`
   - 响应：`{accepted: boolean, agent_response: string, round_number: number}`

3. **POST /api/clarifications/{session_id}/close** - Unity 主动结束澄清
   - 请求体：`{}`
   - 响应：`{destination_id: string, is_closed: true}`

4. **GET /api/destinations/{destination_id}** - 获取目的地清单（核心轮询端点）
   - 响应：`DestinationManifest`（包含规格、场景状态、共享环境）

5. **GET /api/destinations/{destination_id}/scenes/{scene_id}/artifact** - 获取场景产物
   - 响应：`SceneArtifact`（完整场景数据）

6. **GET /api/assets/{sha256_prefix}.{ext}** - 获取资源文件
   - 响应：二进制图片数据，带 `Content-Type` 和 `ETag`

#### 错误响应格式

所有 API 错误统一返回：

```json
{
  "error": {
    "code": "INVALID_SESSION_ID",
    "message": "澄清会话不存在",
    "retryable": false
  },
  "request_id": "req_abc123"
}
```

---

### 模块划分

基于现有 `pilot4mvp2/agent_service` 和 `pilot4mvp/session4` 的架构：

1. **`agent_service/schemas.py`** - 定义所有 Pydantic 模型和 JSON Schema
   - `ClarificationSession`、`DestinationRequirements`
   - `DestinationSpec`、`ScenePlan`、`SceneArtifact`
   - `DestinationManifest`、`SharedEnvironment`
   - API 请求/响应 DTO

2. **`agent_service/storage.py`** - 持久化层接口
   - `save_destination_spec()`、`get_destination_manifest()`
   - `save_scene_artifact()`、`list_scenes_by_destination()`
   - 基于 SQLite 或文件系统实现

3. **`agent_service/app.py`** - FastAPI 路由定义
   - `/api/clarifications/*` 路由
   - `/api/destinations/*` 路由
   - 错误处理中间件

4. **`agent_service/worker.py`** - 后台生成逻辑
   - `generate_destination_spec()`
   - `generate_scene_artifact()`
   - 重试逻辑与状态更新

5. **`tests/test_schemas.py`** - Schema 验证测试
6. **`tests/test_api_contract.py`** - API 集成测试

不新增模块，直接在现有 `pilot4mvp2` 结构上扩展。

---

### Schema 文件组织

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

---

### 与现有代码的集成

1. **复用 `pilot4mvp/session4/content_service/models.py` 的基础类型**：
   - `Point`、`Canvas` 等几何类型
   - `AssetEntry`、`AssetManifest` 资源清单

2. **扩展 `pilot4mvp2/agent_service/storage.py` 的存储接口**：
   - 当前支持会话和 Run 存储
   - 新增目的地、场景和产物的存储方法

3. **对齐 `pilot4mvp2/agent_service/schemas.py` 的 DTO 风格**：
   - 使用 `ConfigDict(extra="forbid")` 禁止额外字段
   - 使用 `Field(min_length=1)` 验证必填字符串
   - 保持与现有 `TextInput`、`CreateRunRequest` 一致的命名风格

---

## Testing Decisions

### 测试原则

**测试外部行为，不测试实现细节**：
- 测试 API 端点的请求/响应契约，不测试内部状态机实现
- 测试 Schema 验证规则（如必填字段、枚举值），不测试 Pydantic 内部逻辑
- 测试状态转换的可观察结果（如 `status` 变化），不测试中间变量

---

### 测试模块

#### 1. Schema 验证测试（`tests/test_schemas.py`）

参考现有 `pilot4mvp/session4/tests/test_models_v02.py` 的风格。

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

参考现有 `pilot4mvp2/tests/test_session4_api.py` 的集成测试风格。

**具体测试用例**：

1. `test_create_clarification_returns_200_with_session_id()`
   - POST `/api/clarifications` → 200 OK
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

基于 issue #9 原型验证的场景，创建集成测试。

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
   - 场景生成超时 → `retryable: true`，`attempt` 递增

2. `test_all_scenes_failed_returns_fallback_destination_id()`
   - 所有场景失败 → `status: "failed"`，提供 `fallback_destination_id`

3. `test_non_scene_step_failure_records_attempt_and_retryable()`
   - 非场景步骤失败（如规格生成失败）记录 `attempt` 和 `retryable`

4. `test_retryable_false_stops_further_attempts()`
   - `retryable: false` 后不再重试

5. `test_error_code_is_stable_enum_value()`
   - `error.code` 使用稳定枚举值（如 `GENERATION_TIMEOUT`）

---

### 测试数据

复用 `pilot4mvp/session4/conftest.py` 的 fixture 模式：

```python
@pytest.fixture
def sample_destination_spec():
    return DestinationSpec(
        destination_id="dest_test_001",
        spec_version="1.0",
        content_version=1,
        title="测试海滩",
        setting="黄昏海边",
        visual_profile=VisualProfile(art_style="pixel_art", ...),
        constraints=Constraints(forbidden_objects=["vehicle"]),
        locked=True,
        locked_at=datetime.now()
    )
```

---

### Prior Art

- `pilot4mvp/session4/tests/test_models_v02.py`：Schema 验证测试模式
- `pilot4mvp2/tests/test_session4_api.py`：API 集成测试模式
- `pilot4mvp/session3/tests/test_pipeline_fail_closed.py`：失败场景测试模式

---

## Out of Scope

### 明确不包含在本规格中

1. **视频生成契约**：静态场景交付完成后另开规格
2. **人工审核与打回协议**：等静态闭环验证后再设计审核流程
3. **共建 UI 交互细节**：本规格只定义数据契约，共建操作界面由 Unity 团队设计
4. **内容安全自动审核**：首版可先跳过，后续作为独立模块集成
5. **scene_state 独立对象**：issue #9 标记为待决策，本规格中 `DestinationManifest.scenes[]` 已足够
6. **描述性文案字段归属**：issue #9 标记为待决策，暂时 `description` 放在 `DestinationSpec` 中

### 明确延期的设计

1. **视频场景（video_scene_package）**：Schema 预留扩展点（`artifact_type` + `video` 字段），但不实现
2. **语义锚点 → 黑圈定位图 → CV → Mask 流程**：协议 3.0 定义，视频闭环时实现
3. **多语言支持**：所有文本字段当前仅支持中文，i18n 延后
4. **目的地浏览与社交功能**：点赞、评论、分享等功能不在首个闭环范围
5. **宠物沟通回应画作**：交互后的动态内容生成延后
6. **旧版本停用协议**：`DestinationSpec` 和 `SceneArtifact` 当前只有单一版本，版本切换逻辑延后
7. **定位图（Locator Image）存储位置**：issue #9 标记为待决策，静态闭环中不涉及（视频闭环专属）

---

## Further Notes

### 与原型的关系

本规格的核心状态机逻辑来自 issue #9 的可交互原型。原型中的 `ContractLogic` 模块提供了纯函数式的状态转换规则，本规格将其形式化为 JSON Schema 和 API 契约。

实现团队可将原型中的 `reducer` 函数作为参考，但不应直接移植 JavaScript 代码。原型的价值在于验证了状态转换的完备性，实际实现应使用 Python 和 Pydantic。

---

### 待决策项的处理

issue #9 识别出 6 个待决策项，本规格对其处理如下：

1. **requirements 映射规则**：四类分类的概念已验证，但 LLM 映射稳定性待实际 Agent 运行验证（见"实现决策 3 - 验证状态"）
2. **scene_state 必要性**：本规格中 `DestinationManifest.scenes[]` 已包含足够状态信息，暂不引入独立对象
3. **定位图和正式 Mask 存储**：静态闭环不涉及（视频闭环专属），当前 `SceneArtifact.assets` 足够
4. **描述性文案归属**：暂时放在 `DestinationSpec.description` 和 `ScenePlan.description`
5. **非场景步骤失败**：定义基础 `attempt` 和 `retryable` 字段，具体策略延期
6. **字段命名约定**：本规格统一采用 `snake_case`

---

### 测试覆盖率目标

- Schema 验证测试：100% 覆盖所有 Pydantic 模型的必填字段和枚举值
- API 契约测试：覆盖所有端点的 2xx 和 4xx 路径
- 状态机测试：覆盖原型中的 5 个引导场景
- 失败场景测试：覆盖所有 `retryable` 分支

---

### 交付物清单

本规格实现后应交付：

1. **JSON Schema 文件**：`contracts/*/v1.0.schema.json`（5 个文件，从 Pydantic 导出）
2. **Pydantic 模型**：`agent_service/schemas.py` 扩展版本
3. **API 路由实现**：`agent_service/app.py` 新增 6 个端点
4. **测试套件**：`tests/test_schemas.py`、`tests/test_api_contract.py`、`tests/test_state_transitions.py`、`tests/test_failure_scenarios.py`
5. **API 文档**：FastAPI 自动生成的 OpenAPI 文档（`/docs` 端点）
6. **Schema 导出脚本**：`scripts/export_schemas.py`

---

### 与 Unity 团队的对齐

实现前需与 Unity 团队确认：

1. 轮询频率：Unity 客户端应多久查询一次 `DestinationManifest`？
2. 缓存策略：Unity 是否实现 `ETag` / `If-Modified-Since` 缓存？
3. 降级体验：`fallback_destination_id` 指向的预置目的地由谁提供？
4. 错误本地化：`error.code` 枚举值是否需要映射表？
5. 坐标系统：Unity 是否接受左下角原点的像素坐标系？

---

### 性能考虑

- `DestinationManifest` 应支持增量更新（如仅返回 `updated_at` 后变化的场景）
- 资源 URI 应使用 CDN 或内容寻址存储，避免热点
- 场景产物大小预期：单个 `SceneArtifact` 约 50-200 KB JSON + 数 MB 图片资源

---

### 安全考虑

- 所有 `destination_id` 和 `scene_id` 应使用不可猜测的随机标识（如 UUID 或 base58 编码）
- 资源 URI 应包含签名或短期令牌，防止未授权访问
- API 应实现速率限制，防止恶意轮询

---

### 与协议 3.0 的关系

本规格是**协议 3.0 的静态闭环子集实现**：

- 保留了协议 3.0 的核心领域对象（DestinationSpec、ScenePlan、SceneArtifact）
- 简化了交付形态（静态图层而非视频）
- 预留了视频扩展接口（`artifact_type` + `video` 字段）

**视频闭环规格的职责**：
- 实现协议 3.0 的完整流程（语义锚点 → 黑圈 → CV → Mask）
- 定义 `video_scene_package` 的完整结构
- 补充 `pet_targets` 的定位语义

静态闭环验证通过后，视频闭环规格应基于本规格扩展，而非推倒重来。
