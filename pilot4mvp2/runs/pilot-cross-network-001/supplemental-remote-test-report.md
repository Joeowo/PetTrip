# Session 7 跨网络 Agent API 测试报告

- 报告日期：2026-08-14
- 会话：Session 7
- 测试范围：`remote_agent_api`
- 测试结论：**通过**（自动化验收 + curl.exe 手工端点测试 + 多轮对话测试均通过）

---

## 1. 测试概览

本轮在**另一台非服务端设备**上，通过公网 HTTPS 消费 PetTrip Agent Service，
覆盖鉴权、Session、真实图片上传、异步 Vision Run 轮询、结构化输出、稳定错误
响应、鉴权文件下载与 SHA-256 校验、多轮对话上下文记忆。除结构化输出在首次
执行时遇到 provider 临时不可用（已恢复）外，其余全部符合预期。

## 2. 测试环境

| 项目 | 说明 |
| --- | --- |
| 远程设备（客户端） | Windows 11 Home 10.0.26200，PowerShell 5.1.26100.8655 |
| 客户端工具 | `curl.exe` 8.12.1（Schannel）、Python 3.12.10 |
| 服务端 | 另一台主机，Agent Service `service_version = 0.7.0-session7` |
| 传输通道 | Cloudflare Quick Tunnel（公网 HTTPS，出站隧道） |
| Base URL | `<redacted>`，SHA-256 = `a673d785a1d23ac060b18dba23a8af1274d49544ecea094891a65ec19cf70ccc` |
| API Key | `<redacted>`（报告与证据均不记录明文） |
| TLS | 系统 CA 校验，关闭自动重定向 |

## 3. 测试范围与方法

1. **自动化远程验收**：`remote-device-bundle/run-session7-remote.ps1`
   （驱动 `session7_remote_acceptance.ps1`），生成脱敏 JSON 报告。
2. **curl.exe 手工端点测试**：按 `pettrip-agent-api.md` 逐端点验证。
3. **结构化输出测试**：`scene_draft` `0.1` 结构化输出 Run。
4. **多轮对话测试**：同一 Session 内三轮连续对话，验证上下文记忆。

---

## 4. 自动化远程验收结果

脚本输出 `remote_api_scope_passed = true`，脱敏报告
`remote-device-bundle/remote-report.json`（schema_version 1.1，session 7），
最新执行时间 `2026-08-14T12:08:30Z`。

| 验收项 | 预期 | 结果 |
| --- | --- | --- |
| 缺失 Key 创建 Session | 401 `AUTHENTICATION_FAILED` | 通过 |
| 错误 Key 创建 Session | 401 `AUTHENTICATION_FAILED` | 通过 |
| 创建 Session | 201 | 通过 |
| 上传真实 PNG（128×64） | 201，mime/sha256 正确 | 通过 |
| 创建 Vision Run | 202 | 通过 |
| 轮询 Run | `queued → running → succeeded` | 通过 |
| Vision 图片内容 | 左红右蓝 | `left=red, right=blue` 通过 |
| 缺失幂等 Key | 400 `VALIDATION_ERROR` | 通过 |
| 资源不存在 | 404 `RESOURCE_NOT_FOUND` | 通过 |
| 未鉴权下载 | 401 `AUTHENTICATION_FAILED` | 通过 |
| 鉴权下载 + SHA-256 | 与源/元数据一致 | 通过（`matches_source/matches_metadata = true`） |

上传与下载校验使用的图片 SHA-256：
`e5945187a0386e9b64d9d7821fbc5cb70a658e97524c80d64f9941ad12357809`。

## 5. curl.exe 手工端点测试结果

按 `pettrip-agent-api.md` 覆盖全部端点，17 项结果如下：

| # | 端点 | 预期 | 结果 |
| --- | --- | --- | --- |
| 1 | `GET /health` | 200 `status=ok` | 通过 |
| 2 | `POST /sessions`（缺 Key） | 401 `AUTHENTICATION_FAILED` | 通过 |
| 3 | `POST /sessions`（错 Key） | 401 `AUTHENTICATION_FAILED` | 通过 |
| 4 | `POST /sessions` | 201 | 通过 |
| 5 | `POST /files`（上传 PNG） | 201，128×64，sha256 匹配 | 通过 |
| 6 | `POST /runs`（缺幂等 Key） | 400 `VALIDATION_ERROR` | 通过 |
| 7 | `GET /runs/run_missing` | 404 `RESOURCE_NOT_FOUND` | 通过 |
| 8 | `GET /files/{id}/content`（无 Key） | 401 `AUTHENTICATION_FAILED` | 通过 |
| 9 | `POST /runs`（图片理解） | 202 | 通过 |
| 10 | `GET /runs/{id}` 轮询 | `succeeded` | 通过 |
| 11 | `GET /files/{id}` 元数据 | 200，无服务端路径 | 通过 |
| 12 | `GET /files/{id}/content` + SHA-256 | 200 且一致 | 通过 |
| 13 | `GET /sessions/{id}/messages` | 200，用户/助手消息齐全 | 通过 |
| 14 | `GET /runs/{id}/events` | 200，事件流完整 | 通过 |
| 15 | `POST /runs`（结构化输出） | 202 → `succeeded` | 首次失败后重测通过 |
| 16 | `POST /runs`（纯文本） | 202 → `succeeded` | 通过 |
| 17 | `GET /docs`、`/openapi.json` | 200 | 通过 |

Run 事件流实测为 `run.queued → run.started → message.created → run.completed`，
与文档一致。

## 6. 结构化输出测试

- 首次两次执行返回 `failed`，错误码均为 `CHAT_PROVIDER_UNAVAILABLE`
  （`retryable = true`，文本模型服务临时不可用），非 `STRUCTURED_OUTPUT_INVALID`，
  属 provider 侧临时抖动。
- 重测 `succeeded`，`output.structured_data` 正确返回 `scene_draft` `0.1`：

```json
{
  "type": "scene_draft",
  "schema_version": "0.1",
  "title": "赤潮之上的蓝色灯塔",
  "theme": "以高饱和红蓝色块构成的极简海岸景观",
  "summary": "炽红天空笼罩海岸，一座深蓝色灯塔矗立在画面右下方的蓝色海岬上…",
  "landmark_kind": "海边灯塔"
}
```

## 7. 多轮对话测试

同一 Session 内连续三轮，验证上下文记忆：

| 轮次 | 用户输入 | 助手回复（摘要） |
| --- | --- | --- |
| 1 | 我家养了一只三岁的柯基，名字叫豆豆，它特别爱追球玩。 | 围绕柯基三岁精力旺盛、爱追球给出运动建议 |
| 2 | 豆豆今年几岁了？它最喜欢做什么？ | 正确回答“三岁，最喜欢追球玩” |
| 3 | 根据豆豆的喜好，推荐周末散步地点。 | 推荐带围栏活动区与开阔草坪的公园，并结合追球喜好 |

`GET /sessions/{id}/messages` 返回完整交替的用户/助手消息历史，上下文记忆正常。

## 8. 发现的问题与处理

| # | 问题 | 影响 | 处理/状态 |
| --- | --- | --- | --- |
| 1 | `session7_remote_acceptance.ps1` 与 `run-session7-remote.ps1` 为 UTF-8 无 BOM，PowerShell 5.1 按系统 ANSI（GBK）误读导致 ParserError | 脚本在远程设备（PS 5.1）无法解析 | 已为两个脚本添加 UTF-8 BOM，可正常解析中文；文档也已注明该要求 |
| 2 | 结构化输出 Run 首次连续两次 `CHAT_PROVIDER_UNAVAILABLE` | 结构化输出不可用（临时） | provider 侧临时不可用，重测已恢复 `succeeded` |

## 9. 结论

另一台非服务端设备通过公网 HTTPS 与 PetTrip Pilot API Key 成功消费 Agent
Service，完成鉴权、Session、真实图片上传、异步 Vision Run 轮询、结构化输出、
稳定错误响应、鉴权文件下载与 SHA-256 校验，以及多轮对话上下文记忆。远程 Agent
API 验收**通过**。

本报告不声明 Unity 主程序或目标 Unity 设备已完成跨网络主链路；该部分需单独
保存并验收 Unity 设备结果。

## 附录

- 验收文档：`session7-cross-network-acceptance.md`
- API 文档：`pettrip-agent-api.md`
- 脱敏验收报告：`remote-device-bundle/remote-report.json`
- 客户端脚本：`remote-device-bundle/session7_remote_acceptance.ps1`、
  `remote-device-bundle/run-session7-remote.ps1`
