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

- 只测试一条主路线：`Responses -> WorldSpec -> ScenePlan -> OpenAI Images ->
  asset manifest -> SceneSnapshot -> Unity`。
- `SceneSnapshot` 是唯一跨 Unity 的内容边界。Unity 不读取 Prompt、OpenAI 请求
  响应、临时路径或模型私有字段。
- 内容服务使用 Python 3.12、FastAPI、Pydantic v2、Pillow、OpenCV 和 SQLite。
- Unity 使用 Unity 6 LTS、URP 2D Renderer、C# 和 `UnityWebRequest`。
- 图片主提供方是 OpenAI Images API；模型 ID 保持配置化，技术路线默认
  `image-2`。
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

### 会话 3：OpenAI -> 内容服务 -> Unity

只在本地链路通过后，接入真实外部模型，明确隔离认证和模型可用性问题。

1. 检查用户是否已提供 `OPENAI_API_KEY`、可用的 Responses 模型，以及 Images 模型
   `image-2` 的访问权限。
2. 任一项缺失时，向用户申请该项，并停止；不得测试猜测的模型 ID，也不得改用假图。
3. 用 Responses Structured Outputs 生成并由 Pydantic 直接校验 WorldSpec。
4. 用 OpenAI Images API 生成概念图，保存为真实 PNG 和 `ImageArtifact` metadata。
5. 用 Pillow/OpenCV 将图片规范化，更新 manifest，构建 Snapshot，并由 Unity 加载。

通过门槛：真实 API 产物可被 Pillow 重新打开，哈希与 manifest 一致，Unity 成功加载；
失败时能区分鉴权、模型不可用、内容策略、超时或解码错误。

### 会话 4：Unity -> 报告/SQLite -> 重放

最后验证运行结果能够返回服务，并在不调用模型的前提下重放。

1. 检查会话 2 或 3 的 artifact 目录和 SQLite 是否可用。
2. 若目录、数据库或既有成功 Snapshot 缺失，申请用户提供缺项并停止。
3. 在 Unity 触发 `pet_wave`，放置 `small_shelter`，保存新的 Snapshot 版本。
4. 重新加载新 Snapshot，生成验证报告并提交给 FastAPI，再写入 SQLite。
5. 重启内容服务，以同一 `run_id` 重建并加载 Snapshot，不再调用 Responses 或 Images。

通过门槛：报告、SQLite 记录和截图的 `run_id` 与 Snapshot 哈希一致；重放期间没有新
模型请求，且重新加载后小窝位置和类型不变。

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

下一次会话从“会话 1：检查 Unity 项目与环境”开始。若环境不存在，将直接说明需要
用户提供的具体内容，不会进行后续实现。
