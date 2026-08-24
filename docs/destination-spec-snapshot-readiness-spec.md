# Destination Spec 事实源与面板前置修复 Spec

## 目的与结论

本文档核实 `agent_service` 当前是否能为 #53 面板提供可信的
Destination Spec 中间产物事实源，并给出可直接交给 `/to-spec` 的修复边界。
本文档只描述决策、契约和验收，不实现 `DestinationSnapshot`、数据库迁移、
API、前端或 SSE。

结论是：当前实现已经具备部分可靠基础，但不能直接把现有 Destination
Manifest 或 Repository 读取结果当作完整事实源。进入 #53 前必须先锁定一个
按 `destination_id + spec_id + spec_version + spec_sha256` 一致读取的投影边界，
并修复哈希、schema、权限和失败审计中的 P0 缺口。

## 现状事实矩阵

下表只记录当前代码能够证明的事实。"可恢复"表示进程重启后可以从 SQLite
和文件记录重建，不表示外部 Provider 任务一定可以继续轮询。

| 对象或字段 | 当前来源与持久化 | 当前读取 | 版本/哈希 | client scope | 重启可见性 |
| --- | --- | --- | --- | --- | --- |
| `destination_requirements` | Requirements JSON、item 行写入 `destination_storage.py:49-75,647-751` | 澄清工作流及 Repository | Requirements 哈希由调用方传入；未在存储层重算 | Destination 公开路由先检查 owner | 大部分可见；整体创建不是单一事务 |
| `DestinationSpec` | `destination_specs` TEXT/整数列，`destination_storage.py:97-112` | `get_destination_spec` 按 destination 取最新，`943-963` | 包含 `spec_version`/`sha256`；哈希由调用方传入，且序列化口径不统一 | Repository 方法不接 client | 可见，但可能读取错误版本 |
| `ScenePlan` | `scene_plans` 独立表，`destination_storage.py:114-127` | `list_scene_plans` 按 destination，`1027-1046` | 不绑定 `spec_id` 或 spec hash | Repository 方法不接 client | 可见，但可能与 Spec 混用 |
| 模板设计 | `agent_service/data/templates/*.json` 与 reference assets；`template_catalog.py:47-87` | 运行时目录加载并校验资产 SHA | 模板目录有 manifest/字节 SHA；legacy 格式仍兼容，`116-230` | 不是目的地公开资产 | 可重载 |
| `shared_environment_spec` | Spec 内自由 JSON TEXT | planning 读取 `environment_design.rendered_prompt/references`，`generation_planning.py:145-173` | 没有统一 DTO/schema 版本 | 不应直接公开内部 Prompt/引用 | 可见，但字段错误可能运行时失败 |
| Shared environment artifact | 文件 + artifact 行，`generation_planning.py:229-355`、`destination_storage.py:1283-1359` | 按 destination 取已有 artifact | 保存图 SHA；未绑定 spec id/version/hash | 文件下载路径有 owner 检查；内部 Repository 无 client | 文件与行可见；不能证明 Spec 版本 |
| Locator/circle | LangGraph state 加文件；`scene_locator.py:212-382` | 工作流内读取 | 环境图 SHA、尺寸、圆几何；最多 3 次内存 retry | 非公开面板事实 | 输入与产物可部分恢复，retry 记录不可恢复 |
| Mask/aperture | 文件注册和 scene workflow；`mask_generation.py:55-218`、`scene_generation.py:224-318` | 最终场景生成读取 | 固定 PNG 参数、SHA、geometry；测试证明字节稳定 | 通过 artifact/file owner 路径保护 | 可恢复 |
| Final SceneArtifact/InteractionZone | 事务创建，`scene_generation.py:504-598` | Manifest 与 artifact API | 关联 scene/destination/render/environment SHA/prompt snapshot | API 下载使用 client-scoped file 读取 | 事务提交后可恢复 |
| `PromptSnapshot` | `destination_storage.py:1211-1277` | 环境和真实 scene render 创建 | model params/references 可记录；scene retry 会产生新的 snapshot | 不公开原始响应/凭据 | 已提交的记录可见 |
| `operation_attempts` | `destination_storage.py:1390-1512` | planning 环境生成使用 | 环境生成覆盖；locator/scene retry 未覆盖 | 内部审计数据 | 已写入记录可见 |
| 异步 Provider task | `async_image_task.py:27-215` 内存 task id/status | 同一调用中同步提交、轮询、下载 | 有 idempotency key；无任务表或 task 状态行 | Provider 外部系统 | 进程中断后不能保证继续轮询 |
| `artifact_ready` | scene workflow 在事务提交后设为 true，`scene_generation.py:590-594` | app 聚合结果，`app.py:262-269` | 技术交付信号，不是视觉质量结论 | 由 destination API owner gate 保护 | 提交的 artifact 可恢复；state 本身不是事实源 |

## 风险核实结果

### 1. Spec 与 ScenePlan 可能混用版本：确认，P0

`clarification_spec.py:407-430` 将模板、Requirements 标识/哈希、
`shared_environment_spec` 和 ScenePlan 内容纳入 Spec 哈希，但 ScenePlan
仍独立存储。`destination_storage.py:878-941` 接收调用方传入的 `sha256`，
`943-963` 只按 destination 读取最新 Spec，`1027-1046` 只按 destination
读取 ScenePlan。更直接的风险在 `generation_planning.py:145-173`：规划阶段
从 destination 取最新 Spec，而不是使用显式 `spec_id`。

因此，重试、恢复或多次生成后，读取路径可能组合新 Spec 与旧 ScenePlan；
Shared environment artifact 也只保存图 SHA，`destination_storage.py:1283-1359`
没有绑定 Spec 版本。现有测试
`tests/workflows/test_generation_planning.py:278-310` 证明同一 destination
的双场景共享路线，但没有证明跨 Spec 版本隔离。

**决策：** #53 前必须引入按 Spec 身份读取的 snapshot seam。所有
Requirements、Spec、ScenePlan 和 artifact 引用必须来自同一个 immutable
snapshot；旧版本不能通过“最新”查询进入新投影。

### 2. 哈希和冻结不变量：确认，P0

Requirements 和 Spec 的创建方法接受调用方哈希，没有在 Repository 中按规范化
内容重算。Spec 创建使用的 JSON 序列化口径与工作流哈希口径也不一致：工作流
显式使用 `sort_keys/ensure_ascii=False`（`clarification_spec.py:253-263`），
而存储层使用默认 `json.dumps`（`destination_storage.py:878-941`）。

`tests/workflows/test_clarification_spec.py:285-324` 直接更新 `sha256` 后仍能
通过，证明当前数据库/应用层没有真正保护冻结内容。测试
`421-447` 也没有执行第二次完整工作流来证明 retry 版本行为。

**决策：** 哈希必须由 canonical DTO 在写入前和读取校验时重算；冻结记录必须
拒绝内容、版本和哈希的修改。哈希失败时投影 fail-closed，不返回“看似实时”的
部分对象。

### 3. `shared_environment_spec` 无稳定公开 schema：确认，P0

`shared_environment_spec` 目前是自由 JSON TEXT（`destination_storage.py:97-112`）。
工作流写入 TemplateCatalog 的渲染结果（`clarification_spec.py:361-403`），
planning 再通过字符串路径读取。没有一个同时覆盖生成、持久化和 HTTP projection
的 DTO/schema；字段缺失的错误主要表现为运行时 `KeyError` 或工作流失败。

TemplateCatalog 同时兼容新 JSON 与 legacy 字段（`template_catalog.py:116-230`）。
这可以作为输入兼容策略保留，但不能让两套形状成为公开输出。

**决策：** 定义版本化的内部 canonical DTO 与受限面板 DTO。legacy 模板只在
输入适配层转换；转换后必须通过 schema、枚举、可空性、引用 SHA 和哈希校验。

### 4. Locator、Mask、aperture、最终场景资产链：基础成立，审计缺口确认，P1

Locator 在 `scene_locator.py:275-382` 做唯一候选、直径和边界校验，最多三次
retry；测试 `tests/workflows/test_scene_locator.py:114-245` 覆盖成功、三次
retry、母图不变和禁止模板/最终图坐标兜底。Mask/aperture 在同一次计算中生成，
`tests/domain/test_mask_generation.py:44-208` 覆盖字节稳定、geometry、边界和
二值内容。最终场景事务在 `scene_generation.py:504-598` 同时创建
InteractionZone 与 SceneArtifact，并关联环境 SHA 和 PromptSnapshot。

但 locator 本身没有创建 locator PromptSnapshot 或 operation attempt；最终场景
retry 也只使用 LangGraph 内存计数（`scene_generation.py:606-661`）。因此产物
本身可恢复，失败尝试的完整审计不可恢复。

**决策：** 保留现有确定性资产链；在 P1 为 locator、scene render 和异步任务
补齐统一 attempt 记录与输入/输出引用。不要把最终点击真值或图像质量判断伪装成
当前 `artifact_ready`。

### 5. 异步任务重启恢复：确认缺失，P1

`async_image_task.py:27-215` 在一个调用中提交、轮询、下载。配置默认关闭
（`shared/config.py:63,125`），没有任务表、持久化 task id/status 或 checkpoint。
现有 `tests/adapters/test_async_image_task.py:16-180` 只覆盖提交、409 幂等、状态
轮询和 failed；没有进程重启恢复测试。启动恢复
`worker.py:309-365` 和 `destination_coordinator.py:32-174` 只能依据已提交
milestone 选择阶段，不能恢复中断的外部 task。

**决策：** P1 明确 external result unknown 状态和持久化 task attempt；在该能力
实现前，面板只能显示 `submitted/unknown/failed` 摘要，不能显示“生成中已确定完成”。

### 6. `artifact_ready` 语义：确认是技术交付，不是质量通过，P0 契约修复

`scene_generation.py:590-594` 只有 artifact 与 interaction zone 事务提交后
才设置 `artifact_ready=true`。它证明文件、尺寸、哈希、关联行已提交；它不证明
宠物存在、位置正确或视觉质量达标。`app.py:674-703` 的 Manifest 进一步用
`done`、`terminal_outcome=succeeded` 和 `all_ready` 计算 `publish_eligible`。
Fixture E2E `tests/test_issue48_fixture_e2e.py:207-250` 证明部分场景失败可聚合为
`partial_scene_failure`。

**决策：** DTO 必须拆分 `artifact_ready`、`delivery_state`、`quality_state` 和
`publish_eligible`。当前没有质量模型时，`quality_state` 必须是 `not_evaluated`，
不能推断为 passed。

### 7. Client scope：HTTP 基础成立，Repository/恢复 seam 缺失，P0

目的地 HTTP 读取先检查 destination owner（`app.py:656-672,705-743`），文件
读取也使用 client-scoped 方法。现有测试
`tests/test_session1_api.py:121-148` 与 `tests/test_session2_image_input_api.py:394-439`
证明 session/run/file ownership 隔离。

但 Destination Repository 的 destination、Spec、ScenePlan 查询不接收
`api_client_id`（`destination_storage.py:943-963,1027-1046`），启动恢复调用
`list_destinations()` 全租户（`destination_coordinator.py:32-43`、
`destination_storage.py:1052-1082`）。这不一定直接造成 HTTP 越权，但新增 snapshot
投影若复用这些方法，容易绕过 owner gate；后台恢复也没有明确租户边界。

**决策：** 所有公开投影必须通过一个 client-scoped service seam；Repository
可以保留内部无 client 版本，但不得由 HTTP handler 直接拼装。后台恢复必须显式
按 worker/service 租户策略运行，并增加跨 client 测试。

### 8. 外部结果未知与状态转移：确认定义不足，P1

`async_image_task.py:176-215` 对非 completed/failed/cancelled 状态持续轮询
直到超时；未知状态没有独立分类。`create_clarification_run` 在
`runs.py:106-223` 同步完成澄清，普通 Run 由 worker 异步；Coordinator
`destination_coordinator.py:45-98` 逐阶段提交，未提交或异常时 fail-closed。

现有模型可以表达部分失败，但没有统一区分普通 Run、澄清同步命令与 Coordinator
阶段状态，也没有把 external unknown 作为可恢复终态公开。

**决策：** 目标 projection 使用分层状态：`run_state`、`clarification_state`、
`destination_phase`、`delivery_state`、`quality_state`。外部任务至少支持
`submitted`、`running`、`completed`、`failed`、`unknown`；`unknown` 不能被转换为
`completed` 或 `artifact_ready`。

## 推荐目标模型

### Immutable snapshot

`DestinationSnapshot` 是一个读取模型，不替代现有写模型。它必须在一个事务
或等价的一致读取边界内，固定以下身份：

```json
{
  "destination_id": "dest_123",
  "spec_id": "spec_123_v2",
  "spec_version": 2,
  "spec_sha256": "sha256:...",
  "requirements": {
    "id": "req_123",
    "sha256": "sha256:...",
    "items": []
  },
  "spec": {
    "schema_version": 1,
    "shared_environment": {},
    "scenes": []
  },
  "artifacts": [],
  "snapshot_state": "consistent"
}
```

实现阶段必须保证：

- `spec_id`、`spec_version` 和 `spec_sha256` 互相校验，不能只取最新行。
- ScenePlan 必须按 `spec_id` 或不可变 Spec 内容绑定；若暂时无法迁移旧表，
  读取层必须验证 ScenePlan 的 canonical hash 与 Spec hash，不一致则返回
  `SNAPSHOT_INCONSISTENT`。
- artifact 必须记录其产生时的 spec identity；旧 artifact 可以展示为历史，
  但不能归入当前 snapshot。
- 所有 canonical JSON 使用同一排序、Unicode 和空值策略；哈希由服务重算。
- snapshot 读取失败时返回结构化错误，不返回混合对象。

### DTO 边界

内部 canonical DTO 必须覆盖 Requirements、Spec、ScenePlan、模板设计、引用
资产和场景产物。公开 projection 只返回面板需要的事实：

- 可公开：稳定 ID、顺序、枚举状态、摘要、尺寸、SHA、引用关系、provider/mock
  标识、attempt 序号、错误代码和是否可重试。
- 只返回摘要或 SHA：PromptSnapshot 的模板版本、参数摘要和引用资产摘要。
- 禁止公开：隐藏思维链、Provider 凭据、原始 Provider 响应、未授权内部文件、
  内部完整 Prompt 文本和任何不属于当前 client 的资产。

### Asset reference 与 attempt

每个资产引用至少包含 `asset_id`、`purpose`、`sha256`、`mime_type`、`width`、
`height`、`source_attempt_id`、`spec_identity` 和 `visibility`。Locator、mask、
 aperture、scene render 和外部 async task 都必须有 `operation_attempt`；每次
retry 创建新 attempt，并关联该次 PromptSnapshot、provider/mock、输入资产、外部
task id/status、输出资产或错误。

`artifact_ready` 只表示 artifact transaction 已提交。质量验证如果未来加入，
只能通过独立 `quality_state` 和验证记录改变质量状态。

## 最小投影契约

### 读取入口

建议新增一个唯一的 client-scoped service 入口，而不是让 #53 直接组合多个
Repository 调用：

```http
GET /api/v1/destinations/{destination_id}/snapshot
Authorization: Bearer <api-key>
```

服务必须先按 `api_client_id` 解析 destination，再以显式 `spec_id` 或当前
immutable revision 读取完整 snapshot。请求支持可选的 `revision`，但不接受任意
内部文件 ID 来绕过授权。

### 成功响应

```json
{
  "destination_id": "dest_123",
  "snapshot": {
    "spec_id": "spec_123_v2",
    "spec_version": 2,
    "spec_sha256": "sha256:...",
    "requirements_sha256": "sha256:...",
    "state": {
      "run": "succeeded",
      "clarification": "closed",
      "phase": "scene_generation",
      "delivery": "partial",
      "quality": "not_evaluated",
      "publish_eligible": false
    },
    "scenes": [],
    "assets": [],
    "attempts": []
  },
  "consistency": {
    "schema_version": 1,
    "status": "consistent"
  }
}
```

### 错误与权限

- 不属于当前 client 或不存在的 destination 统一返回 `RESOURCE_NOT_FOUND`，
  避免泄漏跨租户存在性。
- 版本组合不一致返回 `SNAPSHOT_INCONSISTENT`，并包含可记录的 correlation id，
  不包含内部 Prompt 或 Provider 响应。
- schema、哈希或引用校验失败返回 `SNAPSHOT_INVALID`，禁止降级为最新版本。
- 尚未生成的资产返回明确状态和缺失原因，不伪造 download URL。
- external task 未知返回 `EXTERNAL_RESULT_UNKNOWN` 或等价公开错误状态，不能标记
  为 completed。

## P0/P1/P2 修复路线

### P0：进入 #53 前完成

P0 目标是阻止面板展示混合版本、越权对象或错误的完成语义。

1. **锁定 snapshot identity。** 为 Spec、ScenePlan、Requirements 和 artifact
   projection 增加显式 identity 校验；禁止按 destination 取最新 Spec 与全部
   ScenePlan 的组合读取。
2. **统一 canonical hash。** 由服务根据 canonical DTO 重算 Requirements/Spec
   哈希；拒绝调用方伪造哈希；冻结对象的更新必须失败。
3. **定义 schema/fail-closed。** 为 `shared_environment_spec`、ScenePlan、引用
   资产建立 schema version 和公开 DTO；字段缺失、类型错误或 hash 不一致时不生成
   snapshot。
4. **建立 client-scoped snapshot service。** HTTP handler 只能调用统一 service；
   增加 destination/spec/scene/artifact 的跨 client 404 测试。后台恢复需要显式
   的租户运行策略，不能隐式扫描并公开全租户对象。
5. **固定完成语义。** 将 `artifact_ready`、`delivery_state`、`quality_state`、
   `publish_eligible` 写入契约；明确 `artifact_ready` 不代表质量通过。
6. **补齐 #53 前的验收矩阵。** 逐条映射 #10、#11、#48、#52、#53；当前仓库没有
   #11/#52/#53 的文本证据，必须把对应 issue 原文作为输入，而不是声称已兼容。

受影响的实现边界主要是 `agent_service/storage/destination_storage.py`、
`agent_service/workflows/clarification_spec.py`、
`agent_service/workflows/generation_planning.py`、
`agent_service/api/app.py` 和新增 projection/schema 模块；本阶段不改前端。

### P1：可与 #53 并行，但先写入契约

1. 给 locator、scene render、mask/aperture 和 async task 补齐
   `operation_attempt` 与 PromptSnapshot/asset reference 关联。
2. 持久化外部 task id、status、submitted_at、last_seen_at、idempotency key，
   定义重启后 `unknown`、重新轮询和安全重提交流程。
3. 修复并测试 worker lifespan 中 Coordinator recovery 的注入与启动顺序；当前
   `app.py:163-172` 创建 RunWorker 时未传 Coordinator，而 Coordinator 在后续
   `271` 附近才创建，不能仅凭单元测试宣称启动恢复闭环。
4. 增加部分失败、dispatch 并发、未提交阶段、背景任务取消后重启和真实 Provider
   HTTP contract 测试。Fixture E2E 已覆盖双场景、共享环境 SHA、幂等和部分失败，
   但不覆盖真实 Provider 的完整成功路径。

### P2：明确延期，不得在面板伪装为已有能力

以下能力不阻塞事实源最小闭环，当前面板必须显示为未评估或不可用：

- 完整图像质量模型、宠物存在/位置真值和人工审核协议；
- 完整事件流、SSE/WebSocket、指标体系和跨进程分布式调度；
- Provider 成本、时延、压力、限流和规模门槛；
- 自动内容安全具体实现；
- 视频、音乐、评论、浏览、替代版本和旧版本停用协议。

## 测试验收方案

实现 Spec 时必须先补测试，再以现有测试作为回归基线。最低验收集合如下：

1. 创建两个 Spec 版本后，snapshot 只能返回所选版本的 Requirements、ScenePlan、
   artifact 和引用 SHA；混合版本返回 `SNAPSHOT_INCONSISTENT`。
2. 直接篡改调用方哈希、冻结 Spec 内容或 ScenePlan 绑定时，写入和读取均 fail-closed。
3. 两个 API client 读取同一 destination/spec/artifact 时，非 owner 统一返回 404。
4. 进程重启后，已提交环境、Mask、aperture、SceneArtifact 和 attempt 可重建；
   中断 async task 显示 `unknown`，不会假装 completed。
5. Locator 和 final scene 每次 retry 都有独立 attempt、provider/mock 标识、
   PromptSnapshot 关联和输入/输出引用；重试保持母图与几何输入不变。
6. `artifact_ready=true` 只在 artifact transaction 提交后出现；没有质量验证时
   `quality_state=not_evaluated`；部分失败不得 `publish_eligible=true`。
7. fixture/mock 与真实 Provider 走同一投影 DTO，但响应明确标识 provider mode；
   不返回 Provider 凭据、原始响应或隐藏思维链。
8. 保留现有回归：`tests/workflows/test_scene_locator.py`、
   `tests/domain/test_mask_generation.py`、`tests/workflows/test_scene_generation.py`、
   `tests/test_issue48_fixture_e2e.py` 及 session ownership 测试。

## 与相关 Issue 的兼容性

Issue #10 已固定共享环境不可变、双场景、定位和重试等实现基线；本 Spec 不推翻
这些规则，只把版本 identity、审计和公开边界补齐。Issue #11 的 LangGraph/Coordinator
边界仍成立：checkpoint 不能替代业务真值，snapshot 读取不依赖隐藏 state。

Issue #48 的 fixture/mock 与真实 Provider 共用 HTTP 入口、双场景和部分失败验证可
继续使用；本 Spec 要求 provider mode 显式进入 DTO，并补足真实异步任务未知状态。

仓库检索没有找到 #52、#53 的 issue 正文或本地契约文本，因此不能证明兼容性已经
完成。#53 开工前必须从 GitHub issue 原文导入事件信封、面板状态机和观测字段要求，
再与本文 snapshot 契约逐项对表。若两者冲突，以本 Spec 的安全边界为前置决策，
不得通过放宽 projection 来掩盖不一致。

## 明确不改动范围

本票不实现以下内容：

- 前端面板、React/Unity UI、SSE 或 WebSocket；
- `DestinationSnapshot` 正式生产 API、数据库迁移和完整事件流；
- 图像质量模型、人工审核和最终点击真值检测；
- Provider 凭据、原始响应、隐藏思维链或未经授权资产的公开；
- 视频、音乐、评论、浏览和替代版本协议。

## 交给 `/to-spec` 的最小实现批次

第一批只需建立可信读取前提：canonical DTO/schema、Spec identity 绑定、哈希
重算与冻结保护、client-scoped snapshot service、完成语义和 P0 回归测试。第二批
再补 attempt/async task 持久化与 Coordinator 启动恢复。任何未完成的 P1/P2 能力都
必须在 projection 中明确标记，不得由面板文案推断为事实。
