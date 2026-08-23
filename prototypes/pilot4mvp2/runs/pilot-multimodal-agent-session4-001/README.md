# Agent Service 会话 4 验收证据

本目录记录真实 Chat Provider 的 `scene_draft` `0.1` 正例。当前网关使用
`response_format=json_object`，服务端随后执行独立的版本注册表查找、JSON Schema
校验和固定 DTO 校验。

缺少 `title` 和错误 `type` 负例通过本地 OpenAI-compatible 受控 Provider 注入。
不支持版本由同一正式服务链路验证，并确认在 Provider 调用前失败。API 测试客户端只读取
`output.structured_data` 并使用固定 DTO，不从文本提取 JSON。

证据不包含 API Key、完整鉴权头、Provider 原始响应、SQLite 文件或服务端路径。
