# PetTrip Unity MVP API

> 面向 Patrick / Unity 客户端的第一版 HTTP 接入文档。
>
> 当前文档不绑定服务器地址。部署完成后，将 `BASE_URL` 替换为服务地址，例如 `https://<server-host>`。
>
> 当前服务是 MVP：支持异步澄清对话、目的地生成、双场景 Manifest 查询和图片下载；不要求 Unity 端实现流式连接，使用 HTTP 轮询即可。

## 1. 基本信息

```text
BASE_URL = https://<server-host>
API_PREFIX = /api/v1
```

所有受保护 API 都需要请求头：

```http
X-API-Key: <PATRICK_API_KEY>
```

健康检查不要求 API key：

```http
GET /health
```

服务成功返回：

```json
{
  "status": "ok",
  "service_version": "...",
  "request_id": "..."
}
```

每个响应都可能带有 `request_id`，Unity 端遇到错误时请记录它，便于服务端排查。

## 2. 推荐调用流程

```text
1. POST /api/v1/sessions
2. 重复 POST /api/v1/runs，提交 clarification.submit_input
3. GET /api/v1/sessions/{session_id}/messages，显示 Agent 回复
4. 用户确认信息完整后，发送 clarification.close
5. 轮询 GET /api/v1/destinations/{destination_id}
6. done=true 且 publish_eligible=true 后，读取两个 Scene
7. 使用 scene_artifacts[].download_url 下载图片
```

注意：`POST /runs` 返回 `202 Accepted`，表示 Run 已接受或已创建，不代表最终图片已经生成。

## 3. 创建 Session

### Request

```http
POST {BASE_URL}/api/v1/sessions
X-API-Key: <PATRICK_API_KEY>
```

无请求体。

### Response `201 Created`

```json
{
  "session_id": "session_01...",
  "created_at": "2026-08-25T12:00:00Z",
  "request_id": "req_01..."
}
```

Unity 端保存 `session_id`，后续所有澄清输入都使用它。

## 4. 提交澄清输入

每次提交都必须使用新的 `Idempotency-Key`。同一个 key 只能对应完全相同的一次请求；重试网络超时可以复用同一个 key。

### Request

```http
POST {BASE_URL}/api/v1/runs
X-API-Key: <PATRICK_API_KEY>
Idempotency-Key: unity-session-001-input-001
Content-Type: application/json
```

```json
{
  "session_id": "session_01...",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "unity-session-001-input-001",
    "text": "我想带一只温顺的橘猫去海边旅行，希望画面有白色灯塔和温暖夕阳。"
  }
}
```

推荐的三轮输入示例：

```json
{
  "session_id": "session_01...",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "unity-session-001-input-002",
    "text": "宠物需要在两个场景中清晰可见：一个沿海滩散步，一个在灯塔旁安静休息。"
  }
}
```

```json
{
  "session_id": "session_01...",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "unity-session-001-input-003",
    "text": "整体风格希望温馨、自然、适合做可交互的宠物旅行目的地，两个场景保持同一环境和角色设定。"
  }
}
```

### Response `202 Accepted`

```json
{
  "run_id": "run_01...",
  "session_id": "session_01...",
  "status": "succeeded",
  "request_id": "req_01...",
  "output": {
    "text": "已记录你的需求，下一步……",
    "structured_data": {
      "type": "clarification_turn",
      "schema_version": "1.0",
      "classification": "accepted_wish_input",
      "normalized_text": "……",
      "assistant_reply": "……",
      "captured_facts": ["旅行地点为海边"],
      "missing_dimensions": ["旅行季节"],
      "close_recommendation": false
    }
  }
}
```

Unity 端优先显示：

1. `output.text`（如果存在）；
2. `output.structured_data.assistant_reply`；
3. 同时可以调用消息查询接口获取完整对话。

真实服务中，用户文本会进入 Requirements、Destination Spec 和 ScenePlan；系统固定宠物身份仍由服务端 canonical asset 控制，不能由 Unity 文本覆盖。

## 5. 查询完整澄清对话

### Request

```http
GET {BASE_URL}/api/v1/sessions/{session_id}/messages
X-API-Key: <PATRICK_API_KEY>
```

### Response `200 OK`

```json
{
  "session_id": "session_01...",
  "messages": [
    {
      "message_id": "message_01...",
      "run_id": "run_01...",
      "role": "user",
      "content_text": "我想去海边……",
      "structured_data": null,
      "attachments": [],
      "created_at": "2026-08-25T12:00:00Z"
    },
    {
      "message_id": "message_01...",
      "run_id": "run_01...",
      "role": "assistant",
      "content_text": "已记录……",
      "structured_data": {
        "type": "clarification_turn",
        "schema_version": "1.0",
        "classification": "accepted_wish_input",
        "normalized_text": "……",
        "assistant_reply": "……",
        "captured_facts": [],
        "missing_dimensions": [],
        "close_recommendation": false
      },
      "attachments": [],
      "created_at": "2026-08-25T12:00:01Z"
    }
  ],
  "request_id": "req_01..."
}
```

## 6. 关闭澄清并开始生成

当 Unity UI 认为用户已经确认需求，发送关闭命令。关闭后不能再提交新的澄清输入。

### Request

```http
POST {BASE_URL}/api/v1/runs
X-API-Key: <PATRICK_API_KEY>
Idempotency-Key: unity-session-001-close-001
Content-Type: application/json
```

```json
{
  "session_id": "session_01...",
  "command": {
    "type": "clarification.close",
    "close_request_id": "unity-session-001-close-001"
  }
}
```

### Response `202 Accepted`

成功关闭后，响应中的结构化输出会包含目的地标识（具体字段以当前服务返回为准）：

```json
{
  "run_id": "run_01...",
  "session_id": "session_01...",
  "status": "succeeded",
  "output": {
    "structured_data": {
      "clarification_closed": true,
      "destination_id": "dest_01..."
    }
  },
  "request_id": "req_01..."
}
```

保存 `destination_id`，然后开始轮询目的地状态。

## 7. 查询目的地 Manifest

### Request

```http
GET {BASE_URL}/api/v1/destinations/{destination_id}
X-API-Key: <PATRICK_API_KEY>
```

### Processing response

```json
{
  "destination_id": "dest_01...",
  "phase": "generation",
  "done": false,
  "terminal_outcome": null,
  "publish_eligible": false,
  "scene_plans": [],
  "scene_artifacts": [],
  "request_id": "req_01..."
}
```

### Completed response

```json
{
  "destination_id": "dest_01...",
  "phase": "terminal",
  "done": true,
  "terminal_outcome": "succeeded",
  "publish_eligible": true,
  "scene_plans": [
    {
      "order_index": 0,
      "scene_id": "scene_01..."
    },
    {
      "order_index": 1,
      "scene_id": "scene_01..."
    }
  ],
  "scene_artifacts": [
    {
      "scene_artifact_id": "artifact_01...",
      "scene_id": "scene_01...",
      "render_file_id": "scene_01...",
      "render_mime_type": "image/png",
      "render_width_px": 2048,
      "render_height_px": 1152,
      "render_sha256": "...",
      "interaction_zone_id": "zone_01...",
      "shared_environment_sha256": "...",
      "prompt_snapshot_id": "prompt_snapshot_01...",
      "download_url": "/api/v1/files/scene_01.../content"
    }
  ],
  "request_id": "req_01..."
}
```

只有同时满足以下条件，Unity 才应把目的地视为可发布：

```text
done == true
terminal_outcome == "succeeded"
publish_eligible == true
scene_artifacts 数量 == 2
```

两个 Scene 应共享相同的 `shared_environment_sha256`。Unity 端不需要理解 SHA，只需保留它用于日志和问题排查。

## 8. 下载最终图片

Manifest 中的 `download_url` 是相对于 `BASE_URL` 的路径。

```http
GET {BASE_URL}/api/v1/files/{render_file_id}/content
X-API-Key: <PATRICK_API_KEY>
```

返回：

```http
200 OK
Content-Type: image/png
```

Unity 中建议将图片下载为 `Texture2D`，并根据 `scene_id` 或 `order_index` 缓存。

## 9. 查询单个 Scene

```http
GET {BASE_URL}/api/v1/destinations/{destination_id}/scenes/{scene_id}
X-API-Key: <PATRICK_API_KEY>
```

用于 Manifest 中已有场景的详情查询。Unity MVP 可以只依赖目的地 Manifest；需要单独刷新某个 Scene 时再使用此接口。

## 10. 查询 Run 状态和事件

查询单个异步 Run：

```http
GET {BASE_URL}/api/v1/runs/{run_id}
X-API-Key: <PATRICK_API_KEY>
```

查询 Run 事件：

```http
GET {BASE_URL}/api/v1/runs/{run_id}/events
X-API-Key: <PATRICK_API_KEY>
```

Unity MVP 不要求使用事件接口，轮询目的地 Manifest 即可。

## 11. 轮询建议

建议：

```text
轮询间隔：2 秒
单次目的地最长等待：10 分钟
```

伪代码：

```text
create session
submit clarification inputs
close clarification

repeat until timeout:
    manifest = GET /api/v1/destinations/{destination_id}
    if manifest.done:
        break
    wait 2 seconds

if manifest.publish_eligible:
    download both scene images
else:
    show generation failed state and record request_id
```

不要使用固定次数替代 `done` 判断；Provider 生成时间可能变化。

## 12. 错误处理

错误通常返回：

```json
{
  "error": {
    "code": "...",
    "message": "...",
    "retryable": false
  },
  "request_id": "req_01..."
}
```

Unity 端处理建议：

| HTTP/错误情况 | Unity 行为 |
|---|---|
| `401` / `403` | 停止重试，提示服务配置或 API key 无效 |
| `404` | 检查 session、destination、scene 或 file ID |
| `409` | 检查 `Idempotency-Key` 是否复用了不同请求 |
| `400` | 修正请求体或缺失 header |
| `5xx` 且 `retryable=true` | 使用相同 Idempotency-Key 重试 |
| `done=true` 且 `terminal_outcome != succeeded` | 结束轮询，显示生成失败 |
| 网络超时 | 使用相同 Idempotency-Key 重试，不要生成新 key |

每次错误都记录：

```text
HTTP status
error.code
error.message
request_id
session_id / run_id / destination_id（如果已有）
```

## 13. 当前业务约束

### 固定宠物身份

系统固定宠物资产为：

```text
pet/chongwu-bottom.png
```

用户输入中的“橘猫”只作为用户语义输入保存和处理，不能覆盖系统 canonical PetIdentity。最终场景生成由服务端注入固定宠物 reference。

### 两个 Scene

当前 MVP 固定生成两个 Scene：

1. Scene 0：宠物沿海滩散步；
2. Scene 1：宠物在灯塔旁安静休息。

每个 Scene 的行为必须是确定动作，不能依赖 Unity 在运行时从多个未决动作中选择。

### 视觉锚点和交互区域

服务端会为每个 Scene 生成 locator 和 InteractionZone。当前 Manifest 主要返回 `interaction_zone_id`，Unity MVP 可先把它作为场景交互区域的服务端标识保存；如果需要像素坐标或完整锚点详情，再使用单独的 detail/audit 投影。

## 14. API key 交付和安全

API key 不要提交到 Git、Unity 公共仓库或文档截图中。

服务端部署后，由服务维护者通过私密渠道把以下信息交给 Patrick：

```text
BASE_URL=https://<server-host>
PATRICK_API_KEY=<独立的 Patrick key>
```

Unity 不应把 key 写入公开版本控制。如果当前是内部测试，可以先通过本地配置文件或 CI secret 注入；正式发布前应改成由后端代理或平台安全配置提供。

## 15. 当前不在 MVP 文档范围内

以下能力暂不作为 Unity 接入前置条件：

- SSE/WebSocket 实时事件；
- ETag/304；
- 断线恢复游标；
- 完整任务重启恢复；
- 前端管理面板；
- 自动视觉角色分类模型；
- 复杂历史版本回滚。
