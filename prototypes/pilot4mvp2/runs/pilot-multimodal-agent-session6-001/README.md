# Agent Service 会话 6 验收证据

本目录使用受控 Provider 通过 HTTP API 验证 SQLite 和文件目录的跨进程恢复。同一会话完成两轮对话，服务重启后重新读取已完成 Run、消息历史和生成图片，并比较图片字节哈希。随后在 Provider 执行期间终止服务，重启后遗留 `running` Run 进入 `failed(SERVICE_RESTARTED)`，且没有自动重复调用；新的 Run 成功完成。

验收客户端只访问 HTTP API，不读取 SQLite 或服务端文件目录。证据不包含 API Key、完整鉴权头、SQLite 文件、模型响应或服务端私有路径。
