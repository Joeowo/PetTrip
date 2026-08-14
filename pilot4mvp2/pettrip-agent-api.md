# PetTrip Agent API

本文档说明远程客户端如何通过公网 HTTPS 调用 PetTrip Agent Service。
测试人员只需要 HTTPS Base URL 和 PetTrip API Key，可以使用 Postman、
Apifox、`curl.exe` 或后续 Unity 客户端访问 API。

<!-- prettier-ignore -->
> [!IMPORTANT]
> PetTrip API Key 只用于内部 Pilot 和开发联调。不要把 Key 写入 Git、公开文档、
> 截图、日志或正式 Unity 构建。模型 Provider Key 永远不会提供给客户端。

## 获取接入信息

服务端操作者通过私密渠道向测试人员提供以下两项：

```text
BASE_URL=<public-https-base-url>
PETTRIP_API_KEY=<private-pilot-api-key>
```

当前服务端本机的私密文件位于：

```text
%LOCALAPPDATA%\PetTrip\AgentService\Session7\public-base-url.local
%LOCALAPPDATA%\PetTrip\AgentService\Session7\pettrip-pilot-api-key.local
```

所有受保护请求都必须携带：

```http
Authorization: Bearer <PETTRIP_API_KEY>
```

`GET /health` 不需要鉴权。API 返回的 `download_url` 是相对路径，客户端必须
把它拼接到原始 `BASE_URL`，并继续携带同一 Bearer Key。

## Postman 或 Apifox 配置

在 Postman 或 Apifox 中创建两个环境变量：

```text
base_url=<BASE_URL>
pettrip_api_key=<PETTRIP_API_KEY>
```

对受保护请求使用以下配置：

- Authorization 类型：**Bearer Token**。
- Token：`{{pettrip_api_key}}`。
- Base URL：`{{base_url}}`。
- 自动重定向：建议关闭，避免把 Bearer Key 发送到其他主机。
- TLS 证书校验：必须启用。

## 健康检查

健康检查确认公网入口和 Agent Service 正常运行。

```http
GET <BASE_URL>/health
```

成功响应为 `200 OK`：

```json
{
  "status": "ok",
  "service_version": "0.7.0-session7",
  "request_id": "req_..."
}
```

Windows `curl.exe` 示例：

```powershell
curl.exe --fail-with-body "<BASE_URL>/health"
```

## 创建 Session

Session 保存多轮消息历史。创建请求没有 JSON Body。

```http
POST <BASE_URL>/api/v1/sessions
Authorization: Bearer <PETTRIP_API_KEY>
```

成功响应为 `201 Created`：

```json
{
  "session_id": "session_...",
  "created_at": "2026-08-14T12:00:00Z",
  "request_id": "req_..."
}
```

Windows `curl.exe` 示例：

```powershell
curl.exe --fail-with-body `
  --request POST `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  "<BASE_URL>/api/v1/sessions"
```

保存返回的 `session_id`，创建 Run 时需要使用它。

## 上传图片

图片上传使用 `multipart/form-data`。当前 Pilot 接受真实 PNG 和 JPEG，最大文件
大小、宽高和像素数由服务端限制。

```http
POST <BASE_URL>/api/v1/files
Authorization: Bearer <PETTRIP_API_KEY>
Content-Type: multipart/form-data
```

表单字段如下：

| 字段 | 类型 | 必填 | 值 |
| --- | --- | --- | --- |
| `file` | 文件 | 是 | PNG 或 JPEG |
| `purpose` | 字符串 | 是 | `vision_input` 或 `reference_image` |

Windows `curl.exe` 示例：

```powershell
curl.exe --fail-with-body `
  --request POST `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  --form "purpose=vision_input" `
  --form "file=@C:\path\to\test-image.png;type=image/png" `
  "<BASE_URL>/api/v1/files"
```

成功响应为 `201 Created`：

```json
{
  "file_id": "file_...",
  "source": "user_upload",
  "purpose": "vision_input",
  "mime_type": "image/png",
  "size_bytes": 12345,
  "sha256": "<64-character-lowercase-sha256>",
  "width": 128,
  "height": 64,
  "created_at": "2026-08-14T12:00:00Z",
  "download_url": "/api/v1/files/file_.../content",
  "request_id": "req_..."
}
```

保存返回的 `file_id` 和 `sha256`。

## 创建异步 Run

Run 异步调用模型。创建请求必须包含唯一的 `Idempotency-Key`。同一客户端使用
相同 Key 和相同 Body 时会获得原 `run_id`，不会重复调用模型。

```http
POST <BASE_URL>/api/v1/runs
Authorization: Bearer <PETTRIP_API_KEY>
Idempotency-Key: <unique-request-key>
Content-Type: application/json
```

### 图片理解 Run

以下请求让模型描述刚上传的图片：

```json
{
  "session_id": "session_...",
  "input": {
    "text": "请描述这张图片中的主要内容。",
    "attachments": [
      {
        "file_id": "file_...",
        "purpose": "vision_input"
      }
    ]
  },
  "response_format": {
    "modalities": ["text"]
  }
}
```

Windows `curl.exe` 示例：

```powershell
$body = @'
{
  "session_id": "session_...",
  "input": {
    "text": "请描述这张图片中的主要内容。",
    "attachments": [
      {
        "file_id": "file_...",
        "purpose": "vision_input"
      }
    ]
  },
  "response_format": {
    "modalities": ["text"]
  }
}
'@

curl.exe --fail-with-body `
  --request POST `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  --header "Idempotency-Key: remote-test-001" `
  --header "Content-Type: application/json" `
  --data $body `
  "<BASE_URL>/api/v1/runs"
```

成功响应为 `202 Accepted`：

```json
{
  "run_id": "run_...",
  "session_id": "session_...",
  "status": "queued",
  "request_id": "req_..."
}
```

### 纯文本 Run

不需要图片时，把 `attachments` 设置为空数组：

```json
{
  "session_id": "session_...",
  "input": {
    "text": "推荐一个适合宠物散步的海边旅行地点。",
    "attachments": []
  },
  "response_format": {
    "modalities": ["text"]
  }
}
```

### 结构化输出 Run

请求 `scene_draft` `0.1` 时，结构化数据只从
`output.structured_data` 读取：

```json
{
  "session_id": "session_...",
  "input": {
    "text": "根据图片生成一个海边灯塔场景草案。",
    "attachments": [
      {
        "file_id": "file_...",
        "purpose": "vision_input"
      }
    ]
  },
  "response_format": {
    "modalities": ["text", "structured_data"],
    "structured_output": {
      "schema_name": "scene_draft",
      "schema_version": "0.1"
    }
  }
}
```

结构化 DTO 为：

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

客户端不能从 `output.text` 中提取 JSON 来替代
`output.structured_data`。

### 图片生成 Run

图片生成也使用统一的异步 Run API。客户端不直接访问图片 Provider，也不会收到
Provider 原始 Base64。服务端调用图片 Provider、验证图片、规范化为 PNG、持久化文件，
然后通过 `output.attachments` 返回文件引用。

```json
{
  "session_id": "session_...",
  "input": {
    "text": "生成一张温暖明亮的海边灯塔宠物旅行插画，画面中有一只快乐散步的柯基。",
    "attachments": []
  },
  "response_format": {
    "modalities": ["image"]
  }
}
```

Windows `curl.exe` 示例：

```powershell
$body = @'
{
  "session_id": "session_...",
  "input": {
    "text": "生成一张温暖明亮的海边灯塔宠物旅行插画，画面中有一只快乐散步的柯基。",
    "attachments": []
  },
  "response_format": {
    "modalities": ["image"]
  }
}
'@

curl.exe --fail-with-body `
  --request POST `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  --header "Idempotency-Key: image-generation-001" `
  --header "Content-Type: application/json" `
  --data $body `
  "<BASE_URL>/api/v1/runs"
```

创建响应为 `202 Accepted`。客户端随后轮询 `GET /api/v1/runs/{run_id}`，不能
等待创建请求同步返回图片。图片 Provider 调用可能持续几十秒；客户端超时后继续使用
同一 `run_id` 轮询即可。

成功响应示例：

```json
{
  "run_id": "run_...",
  "session_id": "session_...",
  "status": "succeeded",
  "output": {
    "attachments": [
      {
        "file_id": "file_...",
        "source": "agent_generated",
        "purpose": "generated_image",
        "mime_type": "image/png",
        "size_bytes": 1234567,
        "sha256": "<64-character-lowercase-sha256>",
        "width": 1024,
        "height": 1024,
        "created_at": "2026-08-14T12:00:00Z",
        "download_url": "/api/v1/files/file_.../content"
      }
    ]
  },
  "request_id": "req_..."
}
```

客户端必须使用原 Base URL 拼接相对 `download_url`，继续携带同一 Bearer Key，
并把下载字节的 SHA-256 与附件元数据中的 `sha256` 比较。不要把 `download_url`
作为无需鉴权的公开链接。

### 组合输出 Run

一次 Run 可以同时请求文本、结构化数据和生成图片：

```json
{
  "session_id": "session_...",
  "input": {
    "text": "根据参考图生成一个海边灯塔旅行场景，并输出场景草案和插画。",
    "attachments": [
      {
        "file_id": "file_...",
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

只有三种输出全部准备成功后，Run 才进入 `succeeded` 并提交助手消息。任意一个阶段
失败时，Run 进入 `failed`，不会返回或提交部分成功结果。当前图片生成直接使用
`input.text` 作为图片 Prompt；上传的参考图用于 Chat/Vision，不代表执行了
image-to-image 编辑。

## 轮询 Run

创建 Run 后，使用 Bearer Key 轮询状态：

```http
GET <BASE_URL>/api/v1/runs/{run_id}
Authorization: Bearer <PETTRIP_API_KEY>
```

Windows `curl.exe` 示例：

```powershell
curl.exe --fail-with-body `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  "<BASE_URL>/api/v1/runs/run_..."
```

Run 状态只有以下四种：

```text
queued -> running -> succeeded
                  -> failed
```

建议每 500 毫秒至 1 秒轮询一次，直到进入 `succeeded` 或 `failed`。客户端
超时不应取消服务端 Run；重新请求同一 `run_id` 即可恢复状态。

文本成功响应示例：

```json
{
  "run_id": "run_...",
  "session_id": "session_...",
  "status": "succeeded",
  "output": {
    "text": "图片中是一处海边灯塔旅行场景。"
  },
  "request_id": "req_..."
}
```

结构化成功响应还会包含：

```json
{
  "output": {
    "text": "已生成场景草案。",
    "structured_data": {
      "type": "scene_draft",
      "schema_version": "0.1",
      "title": "潮汐灯塔",
      "theme": "seaside",
      "summary": "一处海边旅行目的地。",
      "landmark_kind": "lighthouse"
    }
  }
}
```

Run 失败响应示例：

```json
{
  "run_id": "run_...",
  "session_id": "session_...",
  "status": "failed",
  "error": {
    "code": "CHAT_PROVIDER_UNAVAILABLE",
    "message": "文本模型服务暂时不可用。",
    "retryable": true
  },
  "request_id": "req_..."
}
```

## 查询文件元数据

查询文件元数据不会返回服务端路径：

```http
GET <BASE_URL>/api/v1/files/{file_id}
Authorization: Bearer <PETTRIP_API_KEY>
```

```powershell
curl.exe --fail-with-body `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  "<BASE_URL>/api/v1/files/file_..."
```

## 下载文件并校验 SHA-256

文件下载接口同样需要 Bearer Key：

```http
GET <BASE_URL>/api/v1/files/{file_id}/content
Authorization: Bearer <PETTRIP_API_KEY>
```

Windows 示例：

```powershell
curl.exe --fail-with-body `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  --output downloaded-image.png `
  "<BASE_URL>/api/v1/files/file_.../content"

(Get-FileHash -Algorithm SHA256 -LiteralPath .\downloaded-image.png).Hash.ToLower()
```

计算结果必须与上传或文件元数据响应中的 `sha256` 完全相同。

## 查询消息历史

按创建时间读取 Session 的用户和助手消息：

```http
GET <BASE_URL>/api/v1/sessions/{session_id}/messages
Authorization: Bearer <PETTRIP_API_KEY>
```

```powershell
curl.exe --fail-with-body `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  "<BASE_URL>/api/v1/sessions/session_.../messages"
```

## 查询 Run 事件

Run 事件可用于排查异步执行进度：

```http
GET <BASE_URL>/api/v1/runs/{run_id}/events
Authorization: Bearer <PETTRIP_API_KEY>
```

```powershell
curl.exe --fail-with-body `
  --header "Authorization: Bearer <PETTRIP_API_KEY>" `
  "<BASE_URL>/api/v1/runs/run_.../events"
```

## 错误响应

普通请求错误使用统一格式：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不合法。",
    "retryable": false
  },
  "request_id": "req_..."
}
```

主要错误码如下：

| HTTP 状态 | 错误码 | 含义 | 建议 |
| ---: | --- | --- | --- |
| `401` | `AUTHENTICATION_FAILED` | Key 缺失或错误 | 检查 Bearer Header |
| `404` | `RESOURCE_NOT_FOUND` | Session、Run 或文件不存在 | 检查资源 ID |
| `400` | `VALIDATION_ERROR` | 请求字段或 Header 不合法 | 修正请求后重试 |
| `409` | `IDEMPOTENCY_KEY_REUSED` | 同一幂等 Key 用于不同 Body | 使用新 Key |
| `400` | `FILE_TYPE_UNSUPPORTED` | 图片类型不支持 | 使用真实 PNG/JPEG |
| `400` | `FILE_TOO_LARGE` | 文件或附件总量过大 | 缩小图片 |
| `400` | `FILE_DECODE_FAILED` | 图片无法解码 | 更换有效图片 |
| `503` | `CHAT_PROVIDER_UNAVAILABLE` | 文本或 Vision Provider 不可用 | 稍后重试 |
| `503` | `IMAGE_PROVIDER_UNAVAILABLE` | 图片生成 Provider 不可用 | 稍后重试 |
| `422` | `STRUCTURED_OUTPUT_INVALID` | 模型结构化结果校验失败 | 调整输入后新建 Run |
| `409` | `SERVICE_RESTARTED` | Run 执行期间服务重启 | 使用新幂等 Key 新建 Run |
| `500` | `INTERNAL_ERROR` | 未分类服务端错误 | 使用 `request_id` 排查 |

远程测试至少覆盖以下错误：

1. 不带 Key 创建 Session，预期 `401 AUTHENTICATION_FAILED`。
2. 使用错误 Key 创建 Session，预期 `401 AUTHENTICATION_FAILED`。
3. 创建 Run 时不带 `Idempotency-Key`，预期 `400 VALIDATION_ERROR`。
4. 查询不存在的 Run，预期 `404 RESOURCE_NOT_FOUND`。
5. 不带 Key 下载文件，预期 `401 AUTHENTICATION_FAILED`。

## Unity 客户端约束

后续 Unity 主程序使用完全相同的 HTTP 契约。Unity 客户端必须：

- 只使用 HTTPS Base URL。
- 在每个受保护请求中设置 Bearer Header。
- 使用 multipart 上传图片。
- 为每次 Run 创建稳定且唯一的 `Idempotency-Key`。
- 处理 `queued`、`running`、`succeeded` 和 `failed`。
- 把相对 `download_url` 固定拼接到原 Base URL。
- 下载文件时继续携带 Bearer Key，并在设备端校验 SHA-256。
- 不直接读取服务器 SQLite、文件目录或 Provider Key。

固定 Pilot Key 不能作为正式玩家凭据。正式版本需要短期令牌或服务端换票机制。

## 在线 OpenAPI

服务运行期间可以通过以下地址查看 FastAPI 自动生成的接口说明：

```text
<BASE_URL>/docs
<BASE_URL>/openapi.json
```

OpenAPI 页面只用于内部 Pilot。不要把包含实际 Base URL 的浏览器地址、截图或导出文件
公开发布。

## 可选自动化验收

`remote_client/session7_remote_acceptance.ps1` 可以自动完成鉴权负例、Session、
程序生成 PNG 上传、Vision Run、轮询、下载和哈希校验，并生成脱敏报告。该脚本使用
UTF-8 BOM，确保 Windows PowerShell 5.1 可以正确解析中文内容。

该脚本只是可选的重复性测试工具。Postman、Apifox、`curl.exe` 或后续 Unity
客户端都可以直接按照本文档调用 API，不依赖自动化验收包。

部署者可以使用以下命令确认当前公网实例不仅存在图片输出接口，而且已经加载可用的独立
图片 Provider：

```powershell
python -m pilot4mvp2.scripts.verify_public_image_api `
  --base-url-file '<private-base-url-file>' `
  --api-key-file '<private-pettrip-key-file>' `
  --timeout 180
```

该命令只执行一次图片生成，轮询到终态，通过公网鉴权下载两次，并验证 PNG、画布尺寸、
文件字节数和三份 SHA-256。它不会打印 Base URL、PetTrip API Key、Provider Key、
Provider Base64 或服务器文件路径。
