# PetTrip 多模态 Chatbot Agent Service Pilot 规格

本文档定义 PetTrip Pilot 阶段的 Agent 服务边界。Pilot 要实现一个可由外部客户端调用的
多模态 Chatbot API 服务：它接收文本和图片，保留会话，异步执行模型调用，并返回文本、
经 Schema 校验的结构化数据和生成图片。

本文件沿用原 `flowise-unity-pilot-spec.md` 路径以保留历史引用，但当前方案不再依赖
Flowise。Flowise 作为已评估但淘汰的实验实现保留在历史分支中；新的 Agent API、数据
模型和验收标准不得包含 Flowise 私有概念。

<!-- prettier-ignore -->
> [!NOTE]
> 这是实验性 Pilot，不是正式生产平台。目标是验证稳定的 Agent 服务边界，不验证
> PetTrip 的正式内容生产 Workflow、Unity 联调、玩家账号系统或规模化部署。

## 1. 目标

Pilot 必须用真实模型和真实文件回答以下问题：

1. 外部客户端能否通过 HTTP(S) 和 Bearer API Key 调用 Agent 服务。
2. Chatbot 能否接收纯文本，并返回文本回复。
3. Chatbot 能否理解客户端上传的图片，并返回文本或结构化结果。
4. Chatbot 能否生成图片，由服务端持久化后供外部客户端下载和校验。
5. Chatbot 能否返回通过 JSON Schema 校验的结构化数据。
6. 长耗时调用能否通过异步 Run 被创建、查询和恢复。
7. 服务端重启后，会话、消息、Run 结果和文件引用能否恢复。
8. 鉴权失败、模型失败、文件非法和结构化输出非法时，客户端能否明确处理。

本 Pilot 不评价 Prompt、美术质量或场景设计质量，也不验证 Unity 与服务的通信线路。
使用 curl、自动化 API 测试或轻量测试客户端完成服务端黑盒验证；只要真实模型和真实图片
完成端到端传输，并留下可重放证据，即形成有效结论。

## 2. 范围与非目标

### 2.1 Pilot 范围

Pilot 包含以下能力：

- 文本输入和文本输出。
- 图片文件上传、图片理解、图片生成和图片下载。
- 版本化结构化输出及服务端 Schema 校验。
- Bearer API Key 鉴权。
- 会话、消息、Run、事件和文件元数据持久化。
- 异步 Run、状态轮询和幂等创建。
- 可选 SSE 事件流；轮询必须独立可用。
- 服务重启后的已完成结果恢复和遗留 Run 处理。
- 面向普通外部客户端的稳定 HTTP 契约和 OpenAPI 文档。
- 提供给 Unity 主程序的 DTO、请求示例、状态机和错误码交接材料；默认情况下，Unity
  客户端实现和联调不属于 Agent Service Pilot。MVP 明确要求跨设备或跨网络演示时，
  通过条件性会话 7 单独验收。

### 2.2 非目标

以下能力不进入本 Pilot：

- 不实现 `WorldSpec -> ScenePlan -> SceneSnapshot` 正式业务 Workflow。
- 不生成或执行 Unity C#、Prefab、Scene 或脚本。
- 不实现正式玩家账号、OAuth、多租户、支付或权限管理后台。
- 不使用 Redis、Kafka、Celery 或多实例 Worker 集群。
- 不实现多 Agent 自由协作或复杂工具编排。
- 不实现图片分层、抠图、场景组装或 Addressables 发布。
- 不实现 Unity 客户端，也不在前六个会话中验收 Unity 与 Agent API 的通信线路。
- 不将图片二进制、模型密钥或完整 API Key 写入 SQLite。
- 不把固定 Pilot API Key 视为正式玩家身份凭据。

## 3. 系统结构

外部客户端只依赖 PetTrip Agent API。Agent 内核、模型提供方和存储实现均位于服务端边界
内，可以在不修改客户端契约的情况下替换。Unity 是后续消费者之一，不是本阶段的测试主体。

```mermaid
flowchart LR
    U[curl / API 测试 / 外部客户端] -->|HTTP(S) + Bearer Key| S[PetTrip Agent Service]
    S --> Q[Async Run Worker]
    Q --> A[Chatbot AgentRunner]
    A --> C[Chat / Vision Provider]
    A --> I[Image Generation Provider]
    S --> D[(SQLite)]
    S --> F[Local File Storage]
    S -.->|可选 SSE| U
```

### 3.1 推荐实现栈

实现可以替换，但首版推荐使用依赖简单、接口清晰的技术栈：

| 层 | 推荐技术 | Pilot 职责 |
| --- | --- | --- |
| HTTP 服务 | Python 3.12、FastAPI、Pydantic | API、DTO、校验、OpenAPI |
| Agent 内核 | 自研薄 `ChatbotAgentRunner` | 历史拼装、模型调用、图片工具调用 |
| 模型 SDK | OpenAI Python SDK 或兼容 HTTP Client | 调用 OpenAI 兼容中转站 |
| 持久化 | SQLite | 会话、消息、Run、事件和文件元数据 |
| 文件存储 | 本地 `data/files/` | 输入图片和生成图片 |
| 图片处理 | Pillow | 解码、校验和画布尺寸规范化 |
| API 测试客户端 | curl、pytest 或等价轻量客户端 | JSON、multipart、轮询、文件下载 |

### 3.2 Provider 边界

Chat/Vision 和图片生成必须使用独立 Provider 接口，因为中转站可能只支持其中一种能力。

```python
class ChatModelProvider(Protocol):
    async def complete(self, request: ChatRequest) -> ChatResult: ...


class ImageGenerationProvider(Protocol):
    async def generate(self, request: ImageGenerationRequest) -> ImageResult: ...
```

首版实现可以分别配置：

- `CHAT_BASE_URL`、`CHAT_API_KEY`、`CHAT_MODEL`。
- `IMAGES_BASE_URL`、`IMAGES_API_KEY`、`IMAGES_MODEL`。

如果同一中转站同时支持两种 API，可以复用 Base URL 和 Key；业务层仍保持两个接口。

### 3.3 已验证的图片生成基线

截至 2026-08-13，Pilot 已实测以下图片生成路径可用：

```text
IMAGES_BASE_URL=https://5202828.xyz/v1
IMAGES_API_KEY=<通过服务端环境变量注入>
IMAGES_MODEL=gpt-image-2
IMAGE_GENERATION_PATH=/images/generations
```

实测结果如下：

| 请求 | 结果 |
| --- | --- |
| `GET /v1/models` | 返回 `200`，模型列表包含 `gpt-image-2` |
| `POST /v1/images/generations` | 返回 `200`，约 36.2 秒，响应包含 `b64_json` |
| `POST /v1/chat/completions` | 返回 `400`，该端点不支持 `gpt-image-2` |

`ImageGenerationProvider` 必须调用 Images Generations API，不能把 `gpt-image-2` 发送到
Chat Completions API。模型名称必须保持配置化；文档或代码中的产品称呼不能替代网关实际
要求的 `gpt-image-2`。

`5202828.xyz` 是本 Pilot 当前可用的图片 Provider。另一个已测试网关 `denxio` 的
`GET /v1/models` 可用，但 `POST /v1/images/generations` 持续返回 `502`，不得作为 Pilot
默认图片 Provider。该结论只覆盖图片生成，不代表 `5202828.xyz` 的 Chat、Vision、
Responses 或结构化输出能力已经通过验证。

### 3.4 已验证的结构化输出基线

截至 2026-08-14，当前 Chat Provider 已实测支持
`POST /v1/chat/completions + response_format(json_object)`。Provider 返回 JSON
字符串后，服务端仍独立执行版本注册表查找、JSON 解析、JSON Schema 校验和固定 DTO
校验。

当前网关的 `response_format(json_schema)` 探针在 60 秒内未返回，不能宣称原生严格 Schema
模式可用。首版把注册的 Schema 作为系统指令发送给模型，并使用 `json_object` 约束响应
格式。模型或网关约束不能替代服务端复验；只有服务端校验通过的数据才能持久化并进入 API
响应。

## 4. Agent 内核

Pilot 内核是一个受约束的多模态 Chatbot，不要求实现通用自主 Agent。它读取历史消息和
输入附件，调用 Chat/Vision 模型，并在请求图片输出时调用图片生成 Provider。

### 4.1 必需能力

`ChatbotAgentRunner` 必须支持：

- 读取同一会话的历史消息。
- 接收用户文本和零到多张图片。
- 返回助手文本。
- 返回零到一个结构化结果。
- 返回零到多张生成图片。
- 将模型和文件错误映射为稳定服务端错误码。
- 不向客户端返回中间推理、密钥、Provider 原始错误或服务器文件路径。

### 4.2 图片生成触发方式

首版不要求模型自由发现任意工具。创建 Run 时由调用方明确声明需要的输出模态；如果
包含 `image`，Runner 直接使用用户文本作为 Prompt 调用图片生成 Provider。使用 Chat
模型改写图片 Prompt 是后续可选增强，不能成为图片输出链路的前置依赖。服务端负责真正的
工具调用、结果保存和错误处理。

```text
用户文本 / 参考图
  -> Chat/Vision 模型生成文本和结构化数据
  -> Runner 将用户文本作为图片 Prompt
  -> Image Generation Provider 生成图片
  -> File Storage 保存图片
  -> Run 返回 file_id
```

这样可以验证 Agent 工具边界，又避免首版陷入开放式工具循环。

### 4.3 图片 Provider 响应处理

`ImageGenerationProvider` 必须按以下顺序处理 Images Generations API 的响应：

1. 读取 `data[*].b64_json`，拒绝缺失或不是合法 Base64 的结果。
2. 使用严格 Base64 解码，并限制解码后的最大字节数。
3. 使用 Pillow 解码图片，校验实际格式、宽高和像素总量。
4. 将图片等比缩放并居中裁切到配置的目标画布尺寸。
5. 以 PNG 保存到临时文件，计算 SHA-256 后原子移动到生成文件目录。
6. 将实际输出宽高、字节数、SHA-256 和相对路径写入文件元数据。

不能假设 Provider 严格遵守请求尺寸。实测请求 `1024x1024` 时返回了 `1402x1122` RGB
图片，因此服务端必须读取实际尺寸并完成规范化，不能把请求尺寸直接写入元数据。

图片生成实测耗时约 36.2 秒。图片 Provider 的服务端调用超时首版设置为 120 秒，并通过
环境变量配置。外部客户端只等待创建 Run 的短请求，然后轮询状态；它不需要保持 120 秒
同步连接。超时后 Run 进入 `failed`，错误码为 `IMAGE_PROVIDER_UNAVAILABLE`，不得自动
重试图片生成，以避免重复计费。

## 5. 数据与文件契约

### 5.1 标识符

服务端生成不透明字符串标识符，并使用稳定前缀便于排查：

```text
client_...  session_...  message_...  run_...  event_...  file_...
```

客户端不得从标识符中解析业务含义。

### 5.2 文件元数据

输入和输出图片共享同一文件资源模型：

```json
{
  "file_id": "file_01J...",
  "source": "agent_generated",
  "purpose": "generated_image",
  "mime_type": "image/png",
  "size_bytes": 248392,
  "sha256": "...",
  "width": 1536,
  "height": 1024,
  "created_at": "2026-08-13T12:00:00Z",
  "download_url": "/api/v1/files/file_01J.../content"
}
```

允许的 `source`：

- `user_upload`
- `agent_generated`

允许的 `purpose`：

- `vision_input`
- `reference_image`
- `generated_image`
- `image_edit_result`

文件必须保存在服务端目录中，SQLite 只保存相对路径和元数据。API 不得返回真实绝对路径。
当 Provider 返回 Base64 图片时，服务端不得把 Base64 写入 SQLite、Run 输出、消息历史、
日志或客户端响应；API 只返回持久化后的 `file_id` 和下载地址。

### 5.3 结构化输出

结构化输出必须由 `schema_name + schema_version` 定位。模型返回后，服务端必须依次执行：

```text
JSON 解析
  -> 查找已注册 Schema
  -> JSON Schema 校验
  -> 校验通过后持久化
  -> 返回 API 客户端
```

首个固定 Schema 使用 `scene_draft` `0.1`：

```json
{
  "type": "scene_draft",
  "schema_version": "0.1",
  "title": "潮汐灯塔",
  "theme": "seaside",
  "summary": "一处可供宠物散步和观察潮汐的海边目的地。",
  "landmark_kind": "lighthouse"
}
```

模型输出 Markdown 代码块或未经校验的 JSON 不得直接作为结构化输出发送给客户端。
客户端必须从专用 `output.structured_data` 字段读取结果，并使用版本对应的固定 DTO 校验；
即使 `output.text` 包含可解析 JSON，也不得将其转换为结构化结果。

## 6. 异步 Run 与事件

图片生成可能超过普通客户端请求时长，因此异步 Run 是 Pilot 的基础协议。创建请求立即
返回 `run_id`，后台 Worker 执行任务，客户端通过轮询恢复状态。

### 6.1 Run 状态机

状态只包含以下四种：

```text
queued -> running -> succeeded
                  -> failed
```

状态只能前进，终态不可修改。Run 成功前不得向消息历史写入不完整的助手消息。

### 6.2 持久化事件

服务端为关键阶段写入 `run_events`。事件类型包括：

```text
run.queued
run.started
image_generation.started
artifact.created
message.created
run.completed
run.failed
```

包含图片输出的成功 Run 必须按以下顺序持久化事件：

```text
run.queued
run.started
image_generation.started
artifact.created
message.created
run.completed
```

`message.created` 只能在全部请求输出准备完成后写入，不能在图片生成前提交部分助手消息。
失败 Run 只保留已经实际开始的阶段事件，并以 `run.failed` 结束。事件用于排错、轮询补充
信息和后续 SSE，不替代 `runs.status` 作为事实来源。

### 6.3 SSE

SSE 是可选增强。即使实现 SSE，客户端也必须能通过 `GET /runs/{run_id}` 恢复状态和最终
结果。SSE 断开不能改变 Run，也不能导致结果丢失。

### 6.4 服务重启规则

服务启动时必须处理遗留状态：

- `queued` Run 保持排队并重新领取。
- `running` Run 标记为 `failed`，错误码为 `SERVICE_RESTARTED`。
- `succeeded` 和 `failed` 保持不变。

首版不自动重试遗留的 `running` Run，避免重复计费和重复生成图片。用户可以使用新的
`Idempotency-Key` 或明确创建新 Run。

## 7. HTTP API

除 `/health` 外，所有接口都使用：

```http
Authorization: Bearer <pilot-api-key>
```

响应使用 `application/json; charset=utf-8`。每个 JSON 响应必须包含 `request_id`。

### 7.1 健康检查

`GET /health` 返回服务状态，不暴露模型、Key、数据库路径或文件路径。

```json
{
  "status": "ok",
  "service_version": "0.1.0",
  "request_id": "req_01J..."
}
```

### 7.2 创建会话

`POST /api/v1/sessions` 创建新的 Chatbot 会话。

```json
{
  "session_id": "session_01J...",
  "created_at": "2026-08-13T12:00:00Z",
  "request_id": "req_01J..."
}
```

### 7.3 上传图片

`POST /api/v1/files` 使用 `multipart/form-data` 上传一张图片。请求包含文件内容和
`purpose` 字段。

服务端必须校验：

- MIME 类型和实际文件签名一致。
- 文件大小未超过配置值。
- 图片可被解码。
- 宽高和像素总量未超过配置值。
- 文件名不能决定服务端路径。

成功后返回文件元数据。首轮允许 `image/png`、`image/jpeg` 和 `image/webp`。

### 7.4 下载文件

`GET /api/v1/files/{file_id}/content` 返回文件内容。下载接口必须鉴权，并使用数据库中的
文件记录定位资源，不能接收任意路径。

`GET /api/v1/files/{file_id}` 返回文件元数据，不返回绝对路径。

### 7.5 创建 Run

`POST /api/v1/runs` 创建异步任务。请求必须包含 `Idempotency-Key` Header；同一客户端、
同一 Key 和同一请求体必须返回原 `run_id`，不能重复调用模型。

```json
{
  "session_id": "session_01J...",
  "input": {
    "text": "根据参考图生成一张海边灯塔旅行场景。",
    "attachments": [
      {
        "file_id": "file_input_01J...",
        "purpose": "reference_image"
      }
    ]
  },
  "response_format": {
    "modalities": ["text", "structured_data", "image"],
    "structured_output": {
      "schema_name": "scene_draft",
      "schema_version": "0.1"
    }
  }
}
```

创建响应：

```json
{
  "run_id": "run_01J...",
  "session_id": "session_01J...",
  "status": "queued",
  "request_id": "req_01J..."
}
```

### 7.6 查询 Run

`GET /api/v1/runs/{run_id}` 返回当前状态；成功时返回全部输出引用。

```json
{
  "run_id": "run_01J...",
  "session_id": "session_01J...",
  "status": "succeeded",
  "output": {
    "text": "已生成潮汐灯塔场景。",
    "structured_data": {
      "type": "scene_draft",
      "schema_version": "0.1",
      "title": "潮汐灯塔",
      "theme": "seaside",
      "summary": "一处海边旅行目的地。",
      "landmark_kind": "lighthouse"
    },
    "attachments": [
      {
        "file_id": "file_output_01J...",
        "source": "agent_generated",
        "purpose": "generated_image",
        "mime_type": "image/png",
        "width": 1536,
        "height": 1024,
        "download_url": "/api/v1/files/file_output_01J.../content"
      }
    ]
  },
  "request_id": "req_01J..."
}
```

失败时返回稳定错误对象：

```json
{
  "run_id": "run_01J...",
  "status": "failed",
  "error": {
    "code": "IMAGE_PROVIDER_UNAVAILABLE",
    "message": "图片生成服务暂时不可用。",
    "retryable": true
  },
  "request_id": "req_01J..."
}
```

### 7.7 查询消息历史

`GET /api/v1/sessions/{session_id}/messages` 按创建时间返回用户和助手消息及其附件引用。
消息历史只返回数据，不能直接触发客户端或游戏行为。

### 7.8 查询事件

`GET /api/v1/runs/{run_id}/events` 返回持久化事件列表。实现 SSE 时，同一路径可根据
`Accept: text/event-stream` 返回事件流；首轮可以只实现 JSON 列表。

## 8. SQLite 数据模型

SQLite 是单进程 Pilot 的持久化存储。服务启动时必须启用：

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
```

### 8.1 最小表

Pilot 使用以下七张表：

| 表 | 主要职责 |
| --- | --- |
| `api_clients` | API Key 哈希、名称、状态和过期时间 |
| `sessions` | 会话创建与更新时间 |
| `messages` | 用户/助手消息、文本和结构化数据 |
| `runs` | 异步状态、幂等键、输入、输出和错误 |
| `run_events` | 可重放的运行事件 |
| `files` | 文件相对路径、来源、用途、MIME、哈希和尺寸 |
| `message_files` | 消息与输入/输出附件的关系 |

### 8.2 数据约束

数据库必须保证：

- `runs.status` 只能使用四种固定状态。
- 同一 `api_client_id + idempotency_key` 唯一。
- 消息角色只能是 `user` 或 `assistant`。
- 文件 `source` 和 `purpose` 使用固定枚举。
- 任务成功、助手消息和附件关系在同一事务中提交。
- 删除数据库记录不能通过路径穿越删除存储根目录以外的文件。

图片二进制保存在：

```text
data/
  agent.db
  files/
    input/
    generated/
```

## 9. 鉴权与安全

Pilot 使用服务端签发的 Bearer API Key。模型提供方 Key 只存在服务端环境变量中，不能
发送到客户端、日志、SQLite、响应或证据文件。

### 9.1 API Key

服务端只保存 Pilot API Key 的哈希，并支持启用、禁用和过期时间。比较 Key 时使用恒定
时间比较。固定 Key 打包进任何客户端后都可能被提取，因此只适用于内部联调。

### 9.2 网络

本地开发可以使用 `localhost` HTTP。跨设备或通过内网穿透联调时必须使用 HTTPS，且
本地服务端口不得同时暴露到公网绕过隧道或反向代理。

### 9.3 文件安全

服务端必须防止：

- 路径穿越和任意文件读取。
- 仅依赖扩展名判断图片类型。
- 超大图片导致内存耗尽或解压炸弹。
- 上传文件覆盖已有文件。
- 未鉴权下载其他客户端文件。

Pilot 可以只有一个内部 API Client，但数据库关系必须保留文件、会话和 Run 的所有者。

## 10. 错误契约

服务端返回稳定错误码，不把 Provider 堆栈和原始响应透传给客户端。

| 错误码 | 含义 | 可重试 |
| --- | --- | --- |
| `AUTHENTICATION_FAILED` | API Key 缺失或错误 | 否 |
| `RESOURCE_NOT_FOUND` | Session、Run 或文件不存在 | 否 |
| `VALIDATION_ERROR` | 请求字段不合法 | 否 |
| `IDEMPOTENCY_KEY_REUSED` | 同一幂等键用于不同请求体 | 否 |
| `FILE_TYPE_UNSUPPORTED` | 文件类型不支持 | 否 |
| `FILE_TOO_LARGE` | 文件超过配置值 | 否 |
| `FILE_DECODE_FAILED` | 图片无法解码 | 否 |
| `CHAT_PROVIDER_UNAVAILABLE` | Chat/Vision Provider 失败 | 是 |
| `IMAGE_PROVIDER_UNAVAILABLE` | 图片生成 Provider 失败 | 是 |
| `STRUCTURED_OUTPUT_INVALID` | JSON 解析或 Schema 校验失败 | 否 |
| `SERVICE_RESTARTED` | Run 执行期间服务重启 | 可重新创建 Run |
| `INTERNAL_ERROR` | 未分类服务端错误 | 视情况 |

所有错误响应和日志都包含 `request_id`；Run 错误同时包含 `run_id`。

## 11. 外部客户端交接

本 Pilot 只交付可被普通 HTTP 客户端消费的服务。Unity 主程序根据交接材料自行实现客户端
和联调，本阶段不要求修改 Unity 工程、验证 `UnityWebRequest` 或提供 Unity 截图。

服务端交付包必须包含：

- OpenAPI JSON 或 YAML。
- Base URL、Pilot API Key 注入说明和本地/内网穿透运行说明。
- Session、文件上传/下载、Run 创建/轮询、消息和事件查询的请求与响应示例。
- `queued/running/succeeded/failed` 状态机和错误码表。
- multipart 上传、鉴权文件下载和 `Idempotency-Key` 使用示例。
- 可选 SSE 的事件格式和断线后的轮询恢复规则。
- `scene_draft` `0.1` DTO 示例，以及结构化输出负例。
- curl、Postman collection 或等价自动化 API 测试集合。

Unity 作为未来消费者必须通过上述 HTTP 契约访问服务，不得直接读取 SQLite、服务端文件
目录或模型提供方密钥；这些约束属于接口交接说明，不属于本 Pilot 的 Unity 验收项。

## 12. 验证会话

会话 1 至会话 6 按风险从低到高验证 Agent Service。前一会话未通过时，后续基础会话
不能标记为通过。会话 7 是条件性的部署验收，独立于前六个会话；执行会话 7 时复用已经
通过的服务端能力和证据，不要求重跑前六个会话。

### 会话 1：服务、鉴权和文本

本会话证明基础 HTTP 和真实文本模型调用成立。

1. 启动服务和 SQLite。
2. 使用无 Key、错误 Key和正确 Key请求受保护接口。
3. 创建会话和纯文本 Run。
4. 轮询到终态并读取助手文本。
5. 保存脱敏请求、响应和日志。

通过条件：无/错 Key 被拒；正确 Key 完成真实模型调用；Run 状态合法；证据不含任何 Key。

### 会话 2：图片输入

本会话证明外部客户端可以上传图片，Chat/Vision 模型可以理解图片。

1. 使用 API 测试客户端上传一张真实 PNG 或 JPEG。
2. 使用返回的 `file_id` 创建 Run。
3. Chatbot 根据图片回答固定问题。
4. API 测试客户端读取文本回复。
5. 上传伪装扩展名和超大图片做负例。

通过条件：真实图片被模型理解；非法图片在调用模型前被拒绝；SQLite 不保存二进制内容。

### 会话 3：图片输出

本会话证明 Chatbot 可以调用图片生成 Provider，并让外部客户端下载结果。

1. API 测试客户端创建要求 `image` 输出的 Run。
2. Runner 直接使用用户文本作为图片 Prompt，并通过
   `POST /v1/images/generations` 调用 `gpt-image-2`。
3. 服务端解码 `b64_json`，校验实际尺寸，并将图片规范化到配置画布。
4. 服务端保存生成图片、实际元数据和 SHA-256。
5. API 测试客户端通过鉴权下载图片，并使用 Pillow 验证格式和目标尺寸。
6. 重复下载同一 `file_id`，确认内容哈希一致。
7. 使用错误端点或无效 Base64 响应执行 Provider 负例测试。

通过条件：Run 返回生成图片引用；客户端能下载并校验真实图片；请求尺寸与 Provider 实际尺寸不一致时仍能得到配置的目标画布；响应和日志不含 Base64、API Key 或服务端路径；Provider 负例进入 `failed`，且不会留下可下载的部分文件。

### 会话 4：结构化输出

本会话证明结构化数据只能通过版本化 Schema 进入客户端。

1. 请求 `scene_draft` `0.1` 输出。
2. 服务端校验并持久化合法结果。
3. API 测试客户端按固定 DTO 校验结果。
4. 分别测试缺少 `title`、错误类型和不支持版本。

通过条件：合法结果完整返回；非法结果进入 `failed`，错误码为
`STRUCTURED_OUTPUT_INVALID`；客户端不从文本中提取 JSON。

截至 2026-08-14，本会话已经通过。真实 Chat Provider 正例返回 `scene_draft` `0.1`，
Run 和助手消息在 SQLite 中保存与 API 一致的结构化对象。缺少 `title` 和错误 `type`
通过本地 OpenAI-compatible 受控 Provider 注入，不支持版本在 Provider 调用前失败；三个
负例均未提交助手消息。脱敏证据保存在
`runs/pilot-multimodal-agent-session4-001/`。

### 会话 5：组合输出

本会话证明一次 Run 可以同时返回文本、结构化数据和图片。

1. 上传一张参考图。
2. 请求 `text + structured_data + image`。
3. 轮询 Run，并检查持久化事件顺序。
4. API 测试客户端分别校验三种输出。

通过条件：三种输出都与同一 Run 和助手消息关联，任何一个失败时不会提交部分成功消息。

截至 2026-08-14，本会话已经通过。真实 Chat/Vision 和图片 Provider 正例上传非敏感参考图，
同一 Run 返回文本、`scene_draft` `0.1` 和规范化 PNG。公共消息历史、事件载荷与 SQLite
共同确认三种输出属于同一助手消息。结构化、文本和图片阶段的受控失败例均未提交助手消息、
生成文件记录或输出附件关系。脱敏证据保存在
`runs/pilot-multimodal-agent-session5-001/`。

### 会话 6：持久化与恢复

本会话证明 SQLite 和文件目录支持重启恢复。

1. 使用同一会话完成至少两轮对话。
2. 通过 API 重新读取历史、Run 和图片引用。
3. 重启服务，重新读取已完成 Run 和文件。
4. 在执行中重启服务，验证遗留 `running` Run 进入 `failed`。
5. 再创建新 Run，确认会话可以继续。

通过条件：已完成结果保持一致；遗留 Run 不会永久卡住或自动重复计费；外部客户端不需要直接访问存储。

截至 2026-08-14，本会话已经通过。受控 Provider 验收客户端通过 HTTP API 在同一
Session 中完成两轮对话，并在服务重启前后重新读取历史、已完成 Run 和生成图片；图片字节数
和 SHA-256 保持一致。执行中强制终止服务后，遗留 `running` Run 在新进程启动时进入
`failed(SERVICE_RESTARTED)`；该 Run 的 Provider 调用没有被自动重复，且新 Run 可以在同一
Session 中成功完成。验收客户端没有直接读取 SQLite 或服务端文件目录。脱敏证据保存在
`runs/pilot-multimodal-agent-session6-001/`，机器可读结论位于
`runs/pilot-multimodal-agent-session6-001/api-tests/recovery-report.json`。

本会话使用受控 Provider 专门验证持久化和恢复边界，不替代会话 5 已完成的真实 Chat/Vision
和图片 Provider 组合输出验收，也不宣称重启期间真实 Provider 调用可以恢复或重试。

### 会话 7：部署与跨网络接入（条件性）

MVP 明确要求跨设备或跨网络演示时执行本会话。本会话先证明远程外部客户端可以通过公网
HTTPS 入口访问 Agent Service。Unity 主程序联调由游戏开发阶段单独执行；只有后续明确要求
Unity 演示时，才增加远程 Unity 设备对同一 API 契约的验收。

1. 通过内网穿透或反向代理提供 HTTPS Base URL，不直接暴露本地服务端口。
2. 在非服务端设备上使用 Bearer Key 创建 Session。
3. 上传一张真实图片，使用返回的 `file_id` 创建异步 Run。
4. 轮询 Run 到终态，并读取文本或结构化结果。
5. 验证缺失鉴权、错误鉴权、请求参数和资源不存在等稳定错误响应。
6. 通过鉴权下载输入图片或生成图片，并校验文件哈希。
7. 保存脱敏的远程请求、响应和网络入口配置。
8. 可选：后续明确要求 Unity 演示时，由 Unity 主程序在远程设备上重复主链路并保存报告。

本轮通过条件：非服务端设备可以通过 HTTPS 完成鉴权、上传、Run 轮询、错误响应和文件下载。
模型 Key、本地端口、服务器私有路径和完整 Bearer Key 不得固化到客户端构建或进入日志、
证据；Bearer Key 只允许通过受保护的临时配置在运行时注入。如果后续要求 Unity 演示，远程
Unity 设备必须另行完成同一主链路。

截至 2026-08-14，本轮远程 Agent API 范围已经通过。操作员在另一台非服务端 Windows 设备上
通过 Cloudflare Quick Tunnel 完成缺失/错误 Key、Session、真实 PNG 上传、异步 Vision Run、
稳定错误响应、鉴权下载和 SHA-256 校验。机器报告的入口哈希和远程脚本哈希与服务端实际值一致，
服务端通过 HTTP API 独立重读 Run、消息、事件、文件元数据和下载内容。核心脱敏证据保存在
`runs/pilot-cross-network-001/`。当前公网实例还通过一次真实图片生成 Run，返回规范化 PNG，
两次鉴权下载与 API 元数据哈希一致；部署证据保存在 `runs/pilot-public-image-api-001/`。

本轮没有执行 Unity 主程序，不生成 `unity-connectivity-report.json`，也不宣称 Unity 跨网络
主链路已经通过。

## 13. 证据目录

每次验证必须保存可复查且脱敏的真实证据：

```text
pilot4mvp2/runs/pilot-multimodal-agent-001/
  README.md
  versions.txt
  deployment-config.redacted.txt
  api-tests/
    authentication.json
    text-run.json
    vision-run.json
    image-output-run.json
    structured-run.json
    combined-run.json
    recovery-report.json
  files/
    input-image.sha256.txt
    generated-image.sha256.txt
    generated-image-metadata.json
  server/
    redacted.log
    recovery.log
  validation-report.json

pilot4mvp2/runs/pilot-multimodal-agent-session6-001/
  README.md
  validation-report.json
  versions.txt
  deployment-config.redacted.txt
  api-tests/
    completed-before-restart.json
    completed-after-restart.json
    interrupted-run.json
    recovery-report.json
  files/
    generated-image.png
    generated-image.sha256.txt
  server/
    first-server.log
    second-server.log
    interrupted-server.log
    recovery-server.log
    provider-calls.jsonl
```

执行条件性会话 7 时，额外保存以下脱敏证据：

```text
pilot4mvp2/runs/pilot-cross-network-001/
  README.md
  https-endpoint.redacted.txt
  remote-client-run.json
  remote-file-hash.txt
  validation-report.json
  evidence-audit.json
  supplemental-remote-test-report.md
  # 后续要求 Unity 演示时才增加 unity-connectivity-report.json

pilot4mvp2/runs/pilot-public-image-api-001/
  README.md
  validation-report.json
  generated-image.sha256.txt
```

证据不得包含模型 API Key、Pilot API Key、Cookie、完整 Authorization Header、SQLite
文件、用户原始隐私图片或服务器私有路径。测试图片必须使用允许进入仓库的非敏感素材。

## 14. 完成定义

完成定义分为 Agent Service 和跨网络端到端演示两层。前者由会话 1 至会话 6 验证；后者
只有在 MVP 明确要求远程演示时才启用，并由条件性会话 7 验证。

### 14.1 Agent Service Pilot 完成

只有以下条件全部满足，Agent Service Pilot 才算完成：

- [x] 外部客户端可以通过 HTTP(S) 和 Bearer Key 调用 Agent Service。
- [x] 异步 Run 可以创建、轮询、成功和失败。
- [x] 外部客户端能发送文本并读取真实模型文本回复。
- [x] 外部客户端能上传真实图片并获得模型理解结果。
- [x] Chatbot 能生成真实图片，外部客户端能鉴权下载并校验。
- [x] 一次 Run 能同时返回文本、合法结构化数据和图片。
- [x] 非法结构化输出不会作为合法 DTO 返回客户端。
- [x] 会话、消息、Run、事件和文件元数据写入 SQLite。
- [x] 图片保存在文件目录而非 SQLite。
- [x] 服务端重启后，外部客户端能恢复已完成结果。
- [x] 遗留 `running` Run 按规则失败，不会永久卡住或重复计费。
- [x] Provider Key 没有进入客户端、日志、数据库或证据；Pilot Bearer Key 没有固化到客户端构建或进入日志、数据库和证据。
- [x] 所有正例和关键负例都有脱敏证据。

通过本 Pilot 可以得出：

> 外部客户端能通过稳定、技术中立的 Agent API 消费多模态 Chatbot 的文本、结构化数据
> 和图片文件，并在异步执行与服务端重启后恢复结果。

不能据此宣称：

> 当前服务已经满足正式玩家鉴权、水平扩展、生产 SLA、内容安全审核或 PetTrip 正式
> 场景生成要求，也不能宣称远程 Unity 设备到服务端的跨网络链路已经通过。

### 14.2 远程 Agent API 验收完成（条件性）

MVP 要求跨设备或跨网络访问 Agent API 时，只有以下条件全部满足，才能宣布远程 Agent API
范围完成：

- [x] Agent Service Pilot 已完成，不需要重跑会话 1 至会话 6。
- [x] Agent Service 通过受控的公网 HTTPS Base URL 提供服务。
- [x] 非服务端设备能完成鉴权、Session、图片上传、Run 轮询、错误响应和文件下载。
- [x] 远程链路的请求、响应、文件哈希和失败信息都有脱敏证据。
- [x] Provider Key、本地端口、完整 Bearer Key 和服务器私有路径没有泄漏到日志或证据。

通过本轮会话 7 后可以额外得出：

> 非服务端设备可以通过公网 HTTPS 入口消费 Agent Service，完成一次可复查的远程 API
> Pilot 验收。

本轮不能据此宣称 Unity 主程序已经完成跨网络联调。如果游戏开发阶段明确要求 Unity 演示，
必须由目标远程 Unity 设备另行完成同一主链路并保存 `unity-connectivity-report.json`。

## 15. 实施顺序

实现按最短纵向链路推进，每次只增加一类主要变量：

1. 实现 `/health`、Bearer 鉴权和统一错误响应。
2. 实现 SQLite 初始化、Session、Message 和纯文本同步模型探针。
3. 实现 `runs`、单进程后台 Worker、轮询和幂等创建。
4. 实现图片上传、文件元数据、鉴权下载和图片安全校验。
5. 接入 Chat/Vision Provider，完成图片理解。
6. 接入 Image Generation Provider，完成生成图片落盘与下载。
7. 实现结构化输出注册表和 JSON Schema 校验。
8. 实现 `run_events`；按需要增加 SSE。
9. 生成 OpenAPI、DTO 示例、curl/Postman collection 和 Unity 交接说明。
10. 执行六个 API 验证会话和服务端重启恢复测试。
11. 如果 MVP 要求远程演示，部署 HTTPS 入口并执行条件性会话 7。

## 16. 下一步

开始实现前，需要锁定以下外部配置，但密钥不得写入仓库或聊天：

- 单独探测 Chat/Vision 中转站的 Base URL、模型 ID 和 API 兼容形态。
- 结构化输出使用已验证的
  `/v1/chat/completions + response_format(json_object)`，并由服务端执行版本注册和 Schema
  复验；当前不得配置为尚未验证可用的 `response_format(json_schema)`。
- 使用已验证的 `IMAGES_BASE_URL=https://5202828.xyz/v1`、
  `IMAGES_MODEL=gpt-image-2` 和 `/images/generations` 路径。
- 将 `IMAGES_API_KEY` 通过服务端环境变量注入，不写入仓库、日志或证据。
- 配置图片 Provider 120 秒超时、目标画布尺寸和解码后最大字节数。
- Pilot API Key 的生成与注入方式。
- 输入文件大小、像素总量和允许 MIME 类型的具体配置值。
- 本地运行端口和内网穿透的 HTTPS 地址。

第一段代码只实现鉴权、SQLite、纯文本 Run 和 `curl` 验证。该链路通过后再增加文件与
图片模型，避免同时调试网络、数据库、Chat/Vision 和图片生成。

### 16.1 会话 2 已锁定参数

会话 2 使用以下输入图片约束，所有值都可通过服务端环境变量覆盖：

- 最大上传大小为 10 MiB（`MAX_UPLOAD_BYTES=10485760`）。
- 图片宽和高分别不超过 4096 像素（`MAX_IMAGE_DIMENSION=4096`）。
- 图片总像素不超过 20,000,000（`MAX_IMAGE_PIXELS=20000000`）。
- 本会话只允许 PNG 和 JPEG；WebP 不进入本轮验收范围。
- 同一 Run 最多引用四张图片，同一 `file_id` 不能重复，且附件总字节数不超过 10 MiB。
- 上传入口在 multipart 解析前完成 Bearer 鉴权和有界请求体读取，避免匿名超大请求先耗尽
  临时磁盘。
- 对话历史保留成功 Run 的文本，但只把当前 Run 的图片附件发送给 Vision 模型，避免每轮
  重复发送历史图片导致请求体和模型成本持续增长。

Chat/Vision Provider 使用 OpenAI-compatible Chat Completions 多模态内容格式。服务端从
本地文件存储读取图片，仅在出站请求内存中临时编码为 `image_url` data URL。Base64 不写入
SQLite、Run、消息历史、日志、响应或证据。真实网关是否接受该格式必须由会话 2 在线验收
确认；离线协议测试不能替代真实模型验证。
