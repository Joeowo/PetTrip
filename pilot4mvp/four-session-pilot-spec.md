# PetTrip 四会话先行测试规格

这是一份先行联通测试规格。目标是分四个会话逐段确认技术栈是否真的能打通，
不是实现完整 MVP，也不评价美术质量。

<!-- prettier-ignore -->
> [!IMPORTANT]
> 每个会话只在上一个会话通过后开始。开始实施前先检查所需环境和资源；若缺少
> Unity 工程、依赖、API Key、模型权限或可用服务，必须向用户说明具体缺项并停止，
> 不能自行配置、安装、用 Mock 替代，或跳到后续环节。

## Problem Statement

当前技术路线定义了从玩家输入到 Unity 景观的多段链路，但尚未证明相邻技术栈之间
能交换真实、可重放的数据。若一次性搭建整条管线，失败时无法判断问题在 Unity、
内容服务、OpenAI 接口，还是资产契约。

## Solution

使用同一个最小“海边灯塔”场景，按四个会话逐段连接。每个会话只有一个通过门槛和
一组必须落盘的证据。首次失败即停止，记录边界，并仅向用户申请完成该步所需资源。

统一场景包含背景、灯塔、活动区、`pet_wave` 互动点和 `small_shelter` 共建槽位。
统一的 Unity 消费边界是版本化 `SceneSnapshot`。

## User Stories

1. 作为研发人员，我想先让 Unity 加载人工准备的 Snapshot，以确认运行时载体成立。
2. 作为研发人员，我想让 Python 服务生成并提供 Snapshot，以确认内容服务和 Unity
   可以通过文件与 HTTP 通信。
3. 作为研发人员，我想接入真实 OpenAI 调用，以确认结构化输入和图片产物可以进入
   同一 Snapshot 边界。
4. 作为研发人员，我想保存、查询并重放一次运行，以确认失败和成功都可以复查。
5. 作为项目负责人，我想在缺少资源时收到明确申请，以避免未验证的假连接被误判为
   通过。

## Implementation Decisions

- 只测试一条主路线：`Responses -> WorldSpec -> ScenePlan -> Images API ->
  asset manifest -> SceneSnapshot -> Unity`。
- `SceneSnapshot` 是唯一跨 Unity 的内容边界。Unity 不读取 Prompt、OpenAI 请求
  响应、临时路径或模型私有字段。
- 内容服务使用 Python 3.12、FastAPI、Pydantic v2、Pillow、OpenCV 和 SQLite。
- Unity 使用 Unity 6 LTS、URP 2D Renderer、C# 和 `UnityWebRequest`。
- 图片生成通过 OpenAI 兼容的 Images API 接入；Base URL 和模型 ID 必须保持配置化。
  技术路线默认 `image-2`，会话 3 已验证的兼容网关实际模型 ID 为
  `gpt-image-2`。
- Vision Worker、ComfyUI、MCP、队列、Addressables 和 Codex 演进闭环不进入这四个
  会话。它们需要在主链通过后单独安排。
- 每次运行使用一个 `run_id`，并保存输入、结构化产物、资产、Snapshot、截图、报告
  和实际版本信息。
- 不自动准备任何环境或凭证。发现缺项时，只报告“缺什么、用于哪一步、需要用户提供
  什么”，得到用户明确答复后才继续。

## Four-session plan

### 会话 1：人工 Snapshot -> Unity

先验证最低运行时边界，避免外部 API 干扰 Unity 问题。

1. 检查 Unity 6 LTS、URP 2D 模板工程和可写的测试目录是否存在。
2. 若任一项缺失，申请对应环境或工程，并停止。
3. 使用人工准备的背景、灯塔和小窝，创建一个最小 `SceneSnapshot`。
4. 让 `SceneSnapshotLoader` 加载图层、活动区、互动点和共建槽位。
5. 保存 Unity 截图和加载日志。

通过门槛：同一模板加载 Snapshot 后能看到背景和灯塔，并可在槽位显示小窝；无
JSON、纹理或 Sprite 创建错误。

### 会话 2：Python 内容服务 -> Unity

在 Unity 已确认可消费 Snapshot 后，再验证 Python 侧的契约与 HTTP 交付。

1. 检查 Python 3.12 及 FastAPI、Pydantic v2、Pillow、OpenCV、JSON Schema 依赖。
2. 若缺少解释器或包，申请用户确认是否准备或安装，并停止。
3. 用 Pydantic 校验固定 WorldSpec，并用固定模板生成 ScenePlan 和 asset manifest。
4. 由 Snapshot Builder 生成通过 JSON Schema 的 Snapshot。
5. FastAPI 用稳定 URI 提供 Snapshot 和 PNG；Unity 用 `UnityWebRequest` 加载。

通过门槛：Unity 只通过 HTTP 取得的文件成功加载，且 Snapshot 不含绝对路径或生成
模型字段。

### 会话 3：外部模型 -> 内容服务 -> Unity

只在本地链路通过后，接入真实外部模型，明确隔离认证、端点兼容性和模型可用性
问题。OpenAI 兼容网关的可用性不能视为 OpenAI 官方端点已经通过验证。

1. 检查用户是否已提供文本侧 `RESPONSES_BASE_URL`、`RESPONSES_API_KEY`、
   `RESPONSES_MODEL`，以及图片侧 `IMAGES_BASE_URL`、`IMAGES_API_KEY`、`IMAGES_MODEL`。
   文本与图片可以使用不同的兼容网关和凭证。
2. 任一项缺失时，向用户申请该项，并停止；不得测试猜测的模型 ID，也不得改用假图。
3. 用 Responses Structured Outputs 生成并由 Pydantic 直接校验 WorldSpec。
4. 用 OpenAI 兼容的 Images API 生成概念图；若返回 `b64_json`，先 Base64 解码，
   再保存为真实 PNG 和 `ImageArtifact` metadata。
5. 用 Pillow/OpenCV 将图片规范化到 `512 x 288`，更新 manifest，构建 Snapshot，
   并由 Unity 加载。

通过门槛：真实 API 产物可被 Pillow 重新打开，哈希与 manifest 一致，Unity 成功加载；
失败时能区分鉴权、模型不可用、内容策略、超时或解码错误。

#### 会话 3 最小输入输出

会话 3 只接入一组已经明确确认的外部模型配置，并沿用海边灯塔固定场景。

- 输入：文本侧 `RESPONSES_BASE_URL`、只存在于环境变量中的 `RESPONSES_API_KEY`、
  `RESPONSES_MODEL`，图片侧 `IMAGES_BASE_URL`、只存在于环境变量中的
  `IMAGES_API_KEY`、`IMAGES_MODEL`（本次为 `gpt-image-2`），以及固定场景文本。
- 中间输出：Responses Structured Outputs 生成且未经人工修补的 `WorldSpec`、原始
  Images 响应、解码后的原始 PNG、`ImageArtifact` metadata，以及规范化后的
  `512 x 288` PNG。
- 最终输出：更新后的 asset manifest、通过 JSON Schema 的 `SceneSnapshot`、Unity
  加载截图和加载日志。
- 禁止输入：猜测的模型 ID、假图、手工修补后的 `WorldSpec`，以及提交到仓库或写入
  日志的 API Key。

#### 会话 3 开始前状态（2026-08-13）

Images 环节已有一个真实可用探针，但 Responses Structured Outputs 环节尚未确认。

- 可用 Base URL：`https://5202828.xyz/v1`。凭证只通过环境变量提供，不写入 Git、
  日志或运行产物。
- `GET /v1/models` 返回 `200`，模型列表包含 `gpt-image-2`。
- `POST /v1/images/generations` 使用 `gpt-image-2` 返回 `200`；一次实测耗时约
  36.2 秒，响应为 `b64_json`。解码后的 RGB 图片为 `1402 x 1122`，Pillow 可重新
  打开，确认不是占位内容。
- 请求尺寸不能视为最终尺寸保证。一次 `1024 x 1024` 请求返回了
  `1402 x 1122`，因此必须以解码后的实际尺寸为准，再由 Pillow/OpenCV 规范化到
  `512 x 288`。
- `gpt-image-2` 不支持 `POST /v1/chat/completions`；该请求实测返回 `400`。图片生成
  必须调用 `POST /v1/images/generations`。
- Images 请求超时必须设置为至少 120 秒，避免把正常生成时间误判为失败。
- 另一个已测试的候选网关虽然能返回模型列表，但 `/v1/images/generations` 持续返回
  `502`，不用于会话 3。

<!-- prettier-ignore -->
> [!IMPORTANT]
> Responses 前置条件已于 2026-08-13 晚间确认：用户指定的 Responses 模型在
> `api.denxio.com` 上支持原生 `POST /v1/responses` Structured Outputs，一次真实
> preflight 返回了未经人工修补且通过 Pydantic 校验的 WorldSpec（证据：
> `runs/session3-preflight-20260813-141620-72ba/`）。Chat Completions
> `response_format(json_schema)` 适配保留为显式 opt-in（`allow_chat_compat`），
> 本轮验收未启用。

#### 会话 3 通过记录（2026-08-15）

会话 3 已通过。真实付费流水线 `run_paid_pipeline.py --confirm-paid` 一次运行完成
Responses + Images 真实调用并全部落盘，随后 Unity 经 HTTP 消费同一 Snapshot。

- 运行：`runs/session3-20260815-023543-bcb8/`（`content-ready.json` 标记）
- Responses：原生 Structured Outputs，`structured_output_api: "responses"`，
  WorldSpec 无人工修补，含 `lighthouse`、`pet_wave`、`small_shelter` 和禁止项
  `vehicle`。
- Images：`gpt-image-2` HTTP 200，返回 `b64_json`；原始 PNG `1774 x 887`，
  Pillow 重开成功，中心裁剪 16:9 后规范化为 `512 x 288`。
- 哈希一致：ImageArtifact、asset manifest 与规范化文件三者 SHA-256 相等
  （`manifest_hash_matches: true`）；原始 PNG 哈希另存于 `raw_sha256`。
- Unity：交付服务 `run_server.py` 从 run 目录提供 Snapshot 与 PNG（不重新调用模型），
  PlayMode 3/3 通过（含会话1/2 回归与 `Session3HttpLoadingTests`），背景纹理断言
  512 x 288，截图见 `runs/session3-unity/unity-screenshot.png`。
- 失败区分：鉴权、模型不可用、内容策略、超时与解码错误在 Provider 层分类为独立
  category，负例由 `tests/test_pipeline_fail_closed.py` 与 `tests/test_external_models.py`
  覆盖；流水线在任何环节失败时拒绝写入 `content-ready.json`。
- 证据扫描：全部落盘证据经凭证扫描（两个 Provider Key 均未出现）；Images 响应中的
  Base64 以不可逆摘要替换（`external/images-call.redacted.json`）。

### 会话 4：Unity -> 报告/SQLite -> 重放

最后验证运行结果能够返回服务，并在不调用模型的前提下重放。

1. 检查会话 2 或 3 的 artifact 目录和 SQLite 是否可用。
2. 若目录、数据库或既有成功 Snapshot 缺失，申请用户提供缺项并停止。
3. 在 Unity 触发 `pet_wave`，放置 `small_shelter`，保存新的 Snapshot 版本。
4. 重新加载新 Snapshot，生成验证报告并提交给 FastAPI，再写入 SQLite。
5. 重启内容服务，以同一 `run_id` 重建并加载 Snapshot，不再调用 Responses 或 Images。

通过门槛：报告、SQLite 记录和截图的 `run_id` 与 Snapshot 哈希一致；重放期间没有新
模型请求，且重新加载后小窝位置和类型不变。

#### 会话 4 通过记录（2026-08-15）

会话 4 已通过。两阶段编排 `run_unity_session4.py` 完成统一输入、Unity 交互、
v2 放置、报告回传与重启离线重放（`ALL_CHECKS_PASSED`；review 修复后复跑，
最终 run `session4-20260815-123037-c247`）。

- 契约演进：`contracts/scene-snapshot/v0.2.schema.json` 在 v0.1 基础上为
  `build_slots` 增加可选 `placed_prefab`（省略 = 未放置）。v0.1 快照仍通过原
  Schema（回归用例覆盖），Unity 渲染 v0.2 起由字段驱动。
- 上游输入验收：源 run 必须携带既有成功 Snapshot——`create_run` 缺失即拒绝并
  停止，复制为 `source-scene-snapshot.json`；物化时重建结果必须与该基线业务
  字段一致（版本与放置状态除外），被篡改的基线触发 fail-closed 拒绝物化。
- 统一输入：`POST /runs`（`run_id` + 输入）返回相同 ID，落盘 `input.json` 并写
  SQLite `job.accepted`；随后仅从源 run artifact（world-spec + scene-plan +
  assets）确定性物化 Snapshot，全程零模型调用。缺输入负例返回 `422` 且不创建
  运行目录（编排内实测）。
- SQLite 前置验收与保留：编排启动即验证数据库——必须已存在且可查询（缺失或
  不可查询即打印缺项并以非零码停止，申请用户提供，不做任何静默初始化）；
  通过后历史记录全部保留，本次只追加。复跑实证追加语义：第二次编排开场
  `existing-verified`（`job_events=4`，含前次记录），结束 `6`。
- Unity 运行时：宠物区内移动被接受、活动区外目标被拒绝且位置不变；`pet_wave`
  可触发；`PlacePrefab` 仅接受槽位 `allowed_prefabs` 内的 Prefab（`rocket` 被
  拒绝），行为全部由 Snapshot 字段驱动。
- v2 保存重载：放置后上传 `scene-snapshot-v2.json`（服务端执行 v0.2 Schema +
  业务字段一致性校验，越权修改业务字段返回 `422`），清空场景仅用 v2 重载，
  小窝位置 `(430, 96)` 与类型 `small_shelter` 不变；v1 加载路径由 EditMode
  回归保留。
- 报告回传：Unity 报告显式携带 `run_id`（服务端校验与目标 run 一致，不一致
  返回 `409`）与 `snapshot_sha256`（与活动快照一致），截图 Base64 解码后经
  Pillow 重开（512 x 288 PNG）落盘；`unity-report.json`、SQLite
  `validation_reports` 与截图三方 `run_id` 与 Snapshot 哈希（`67d856ef…`）
  可直接交叉核验。
- 离线重放：服务进程重启（干净环境，无任何 `OPENAI_*`/`RESPONSES_*`/
  `IMAGES_*` 变量），`POST /runs/{id}/replay` 仅从既有 artifact 重建 v2 并写入
  `job.replayed`；SQLite 事件序列恰为 `job.accepted` + `job.replayed`，事件
  明细 `model_calls: none`，服务日志无模型端点痕迹；重放快照哈希与重启前一致。
- 测试：session4 pytest 38 通过（正例/负例/重启重放/源快照基线 fail-closed/
  报告 run_id 校验/SQLite 缺失与损坏前置 fail-fast）；Unity EditMode 14/14
  （含 v0.1 回归）；PlayMode 交互流
  与重放加载各 1/1 通过；session2（18）与 session3（54）pytest 分目录回归全绿。
- 证据：`runs/session4-20260815-123037-c247/`（最终 run 全套产物，含
  `source-scene-snapshot.json` 基线）、`runs/session4-20260815-115620-0a9e/` 与
  `runs/session4-20260815-123004-a82d/`（历史 run 保留）、
  `runs/session4-unity/`（两份 PlayMode XML、日志、双截图、SQLite 查询快照
  含全库 run 清单）。

## Testing Decisions

- 测试只判断外部可观察行为：接口响应、已校验的 JSON、实际图片文件、Unity 截图、
  SQLite 查询记录和重放时的请求记录。
- 不测试内部函数如何实现，也不以代码覆盖率作为本次先行测试指标。
- 每个会话只允许使用上一步的真实产物；不手工修改失败的 JSON 或复制文件绕过接口。
- 负例仅用于确认契约拒绝行为，例如缺少输入必须返回 `4xx` 且不创建运行目录。
- 每次失败记录 `输入 -> 调用 -> 实际输出 -> 期望输出`，连同组件版本和请求 ID
  写入报告，但绝不写入 API Key。

## Out of Scope

本规格不包括多场景对照、视觉评分、自动重试策略、视觉分割质量、ComfyUI、Vision
Worker、MCP、Codex 改进 Workflow、多人、经济系统、开放世界、Addressables、队列
或正式部署。

## Further Notes

本仓库当前没有远程仓库或可用的任务追踪器配置，因此无法按 `to-spec` 的要求发布外部
事项或添加 `ready-for-agent` 标签。本文件是对应的本地规格；如后续提供任务系统，
可原样发布并添加该标签。

下一次会话从“会话 4：Unity -> 报告/SQLite -> 重放”开始。会话 1、2、3 已确认通过；
会话 4 前置条件为会话 2 或 3 的 artifact 目录与 SQLite。开始前先检查
`runs/session3-20260815-023543-bcb8/`（含 `content-ready.json`）与 SQLite 可用性，
缺项时向用户说明并停止。
