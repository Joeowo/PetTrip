# AgentRunner 架构边界

本文档记录 PetTrip Agent 服务的核心架构约定：外层服务稳定依赖项目自定义的
`AgentRunner` 契约，pi-agent、LangGraph 或其他 Agent 框架只是该契约的内部实现。
这样可以先用轻量内核快速完成 MVP，又不把 HTTP API、持久化和客户端协议绑定到某个
Agent 框架。

<!-- prettier-ignore -->
> [!IMPORTANT]
> 可替换的是 Agent 内核，不是整个服务。鉴权、会话、异步 Run、文件、持久化和错误契约
> 由外层服务统一负责，不随 Agent 框架一起更换。

## 1. 决策

PetTrip 在 Agent 内核与服务外围之间定义一个最小、稳定的 `AgentRunner` 接口。外层服务
只调用这个接口，不直接依赖 pi-agent 的消息类型、LangGraph 的 Graph State、框架专用事件
或 checkpoint 格式。

```text
HTTP API / Bearer 鉴权 / SQLite / 文件 / 异步 Run
                         |
                    RunExecutor
                         |
                 AgentRunner 契约
              /           |           \
DirectProviderRunner PiAgentRunner LangGraphRunner
```

当前 MVP 先用 `DirectProviderRunner` 完成简单对话和受控工具调用，再通过同一契约接入
`PiAgentRunner`。未来正式 Workflow 出现复杂分支、人工审核、暂停和恢复需求时，可以
增加 `LangGraphRunner`，而不修改外部 API 契约。

## 2. 职责边界

职责必须按服务外围、执行协调和 Agent 内核三层划分。

### 2.1 外层服务

外层服务提供稳定、可被普通 HTTP 客户端调用的产品能力。它负责：

- HTTP API 和 OpenAPI 文档。
- Bearer API Key 鉴权。
- Session、Message、Run、Event 和文件元数据持久化。
- 异步任务创建、轮询、可选 SSE 和幂等处理。
- 图片上传、下载、校验和文件存储。
- 服务端错误码、日志关联和重启恢复。

外层服务不得要求客户端理解任何 Agent 框架的内部概念。

### 2.2 RunExecutor

`RunExecutor` 连接持久化任务与 Agent 内核。它负责：

- 从数据库领取 `queued` Run。
- 组装标准化的 `AgentInput`。
- 调用选定的 `AgentRunner` 实现。
- 将 `AgentEvent` 映射为持久化事件和可选 SSE。
- 将最终结果写入消息、Run 和文件记录。
- 将框架或 Provider 错误映射为稳定服务端错误码。

`RunExecutor` 不负责模型推理策略，也不暴露框架原始事件。

### 2.3 AgentRunner

`AgentRunner` 只负责一次 Agent 执行。它接收标准化输入，调用模型与允许的工具，并输出
标准化事件和结果。

```python
class AgentRunner(Protocol):
    def execute(self, input: AgentInput) -> AsyncIterator[AgentEvent]: ...
```

具体字段在实现阶段通过版本化 Schema 固定，但至少需要表达：

- 用户文本和会话历史。
- 输入图片的 `file_id`、MIME 类型和受控读取方式。
- 请求的输出模态，例如文本、结构化数据和图片。
- 文本结果、经校验的结构化结果和输出文件引用。
- 执行阶段、工具调用结果和稳定错误信息。

## 3. 自有领域契约

框架适配层必须把内部对象转换成项目自有领域对象。建议保持以下最小对象集合：

| 对象 | 用途 |
| --- | --- |
| `AgentInput` | 描述一次执行的历史、输入附件和输出要求 |
| `AgentEvent` | 描述标准化执行事件，不暴露内部推理 |
| `AgentOutput` | 描述文本、结构化数据和文件引用 |
| `StructuredOutput` | 描述 Schema 名称、版本和校验后的数据 |
| `FileReference` | 描述 `file_id`、用途、MIME、哈希和下载引用 |
| `AgentError` | 描述稳定错误码、公开消息和是否可重试 |

以下框架对象不得越过 `AgentRunner` 边界：

- pi-agent 的原生 message、tool call 或事件对象。
- LangGraph 的 State、Node、Edge、Command 或 checkpoint 数据。
- 模型 Provider 的原始请求、响应、堆栈和密钥。
- 服务器绝对文件路径或图片 Base64 二进制。

## 4. pi-agent 与 LangGraph 的位置

两个方案解决的复杂度不同，不需要在项目早期做永久二选一。

### 4.1 pi-agent

pi-agent 适合当前 MVP 的简单 Chatbot 内核：

- 对话循环和有限工具调用直接。
- 依赖较少，便于快速调试中转站模型。
- 事件可由适配器转换为统一的 `AgentEvent`。
- 适合先验证文本、图片输入、结构化输出和图片生成。

pi-agent 不承担 HTTP 服务、鉴权、SQLite、文件存储、异步 Run 和重启恢复。

### 4.2 LangGraph

LangGraph 适合后续需要显式推理图的流程：

- 多步骤条件分支。
- 人工审核后的暂停与继续。
- 某一步失败后的定向重做。
- 长流程状态和 checkpoint。

LangGraph 的 checkpoint 只保存编排内部状态，不能替代业务数据库中的 Session、Run、文件
和产物记录。长耗时图片任务所需的可靠任务执行机制也必须独立设计。

## 5. 与正式 Workflow 的关系

Agent 只负责需要模型判断的环节。确定性业务流程仍使用普通代码、版本化 Schema 和任务
状态机实现。

```text
用户输入
  -> Agent：澄清意图和生成 WorldSpec
  -> Workflow：校验、拆解和创建素材任务
  -> Agent/模型工具：生成或评估素材
  -> Workflow：规范化、版本管理和质量校验
  -> Agent：必要时给出修订策略
  -> Workflow：生成 ScenePlan / SceneSnapshot
```

素材落盘、格式转换、Schema 校验、哈希、版本管理和 Unity 导出都是确定性业务能力，不能
隐藏在 Agent Prompt 或框架状态中。

## 6. MVP 能力覆盖评估

本节评估的是按照当前 Pilot 规格全部实现后的设计覆盖度，不代表当前代码完成度，也不代表
生产就绪度。百分比用于说明剩余工作量级，不能替代验证会话和完成定义。

| 能力 | 预计覆盖 | 说明 |
| --- | ---: | --- |
| HTTP、OpenAPI 和稳定 DTO | 90% | 外部契约不包含 Agent 框架专用字段 |
| Bearer 鉴权 | 70% | 满足内部 Pilot，不是正式玩家鉴权 |
| Session、Message、Run 和 SQLite | 85% | 单进程场景完整，不覆盖多实例一致性 |
| 异步 Run、轮询、幂等和重启恢复 | 80% | 不覆盖分布式租约、取消和自动扩缩容 |
| 图片上传、理解、生成和下载 | 90% | 文件契约、哈希和安全边界较完整 |
| 版本化结构化输出 | 80% | Schema 路径明确，真实模型兼容性仍需验收 |
| Agent 框架替换 | 60%-70% | 架构原则已确定，执行契约和替换测试仍需补齐 |
| 生产部署能力 | 30%-40% | 多租户、SLA、内容审核和水平扩展均为非目标 |

按照这一口径，完成当前 Pilot 后，外层服务可以覆盖约 80%-85% 的可复用 Agent 服务基础
能力。它足以支持 MVP 和后续 Runner 接入，但不能仅凭功能会话通过就宣称 pi-agent、
LangGraph 和其他内核已经可以无成本互换。

## 7. MVP 必须封闭的 Runner 边界

在把 `AgentRunner` 认定为真实可替换边界前，MVP 必须完成以下四项。它们不要求建设通用
插件系统，但必须形成代码契约和自动化测试。

1. 固定最小领域契约。定义版本化的 `AgentInput`、`AgentEvent`、`AgentOutput`、
   `AgentError` 和 `FileReference`，禁止框架原生对象越界。
2. 引入 `RunExecutor`。由它读取 Run 和会话历史、组装 `AgentInput`、调用 Runner、映射
   事件和错误，并原子持久化最终结果；Runner 不直接访问 SQLite。
3. 固定文件能力边界。向 Runner 提供项目自有的受控 `FileReader` 和
   `ArtifactWriter`，Runner 只使用 `file_id` 和 `FileReference`，不返回绝对路径、Base64
   或直接写业务文件表。
4. 增加 Runner 契约测试。使用 `FakeAgentRunner`、`DirectProviderRunner` 和至少一个真实
   框架适配器运行同一组测试，确认 HTTP API、SQLite Schema、文件契约和错误响应无需修改。

历史消息由 `RunExecutor` 读取并作为 `AgentInput` 传入。Runner 可以决定如何把标准化历史
转换成框架消息，但不能自行查询业务数据库。Runner 返回项目定义的 `AgentError`；最终
HTTP 错误码和 Run 状态由 `RunExecutor` 统一映射。

## 8. MVP 落地建议

当前阶段采用与 Agent Service Pilot 主规格一致的最短实现路径。已经形成的 Python、
FastAPI、SQLite 和文件存储外围继续保留，不为了接入某个 Agent 框架重写服务。

1. 保留 Python 3.12、FastAPI、SQLite 和单进程 Worker 作为稳定外围。
2. 定义最小领域契约，并把 Worker 的执行协调职责收敛为 `RunExecutor`。
3. 将当前直接调用 Chat/Vision 和图片 Provider 的逻辑包装为
   `DirectProviderRunner`，先完成 Pilot 功能会话。
4. 实现 `PiAgentRunner`，把 pi-agent 消息、事件和工具结果转换成项目领域对象。如果
   pi-agent 运行时不能进入 Python 进程，则使用仅服务端可访问的私有本地 RPC 适配层，
   公开 HTTP API 保持不变。
5. 使用 curl 和自动化 API 测试验证服务，并用 Runner 契约测试验证替换能力；Unity 联调
   仍按主规格的条件性部署会话处理。
6. 只有在正式 Workflow 出现真实分支、暂停和人工审核需求后，再实现
   `LangGraphRunner`。

首轮不建设动态 Runner 注册中心、复杂依赖注入框架或跨框架状态迁移。一个 Python
`Protocol`、一个默认实现、构造参数注入和一组共享契约测试已经足够。

```text
FastAPI / SQLite / 文件 / 异步 Run
                 |
             RunExecutor
                 |
             AgentRunner
          /          |          \
DirectProviderRunner PiAgentRunner LangGraphRunner
```

## 9. 约束与验收

以下条件用于判断模块化是否真实成立：

- 外部 API 的请求和响应中没有 pi-agent 或 LangGraph 专用字段。
- SQLite 业务表不直接保存框架对象或 checkpoint 作为唯一事实来源。
- `RunExecutor` 可以通过配置或构造参数替换 `AgentRunner` 实现。
- 替换 Runner 后，鉴权、Session、Run、文件和错误 API 不需要修改。
- Agent 输出的结构化数据必须经过项目 Schema 校验后才能持久化和返回。
- 图片只通过 `file_id` 和文件元数据跨边界传递。
- Agent 内核失败时，外层服务仍能返回稳定的 Run 状态和错误码。
- 同一组 Runner 契约测试可以在 `FakeAgentRunner`、`DirectProviderRunner` 和
  `PiAgentRunner` 上运行。
- 功能验证会话通过只能证明服务行为可用；Runner 替换测试通过后，才能证明框架解耦成立。

## 10. 后续决策点

开始实现时需要继续确定以下细节：

- `AgentEvent` 首版是否包含增量文本，还是只保留阶段事件和最终结果。
- pi-agent 使用的具体包、版本、模型 Provider 兼容方式，以及 `PiAgentRunner` 采用进程内
  适配还是私有本地 RPC 适配。
- 单进程 Worker 的领取、并发和服务重启规则。
- 引入 LangGraph 的触发条件，以及是否使用独立运行时服务。

在这些问题确定前，`AgentRunner` 边界仍保持有效，不需要提前锁定未来的 Agent 框架。
