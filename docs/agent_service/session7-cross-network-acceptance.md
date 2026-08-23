# 会话 7 跨设备 API 验收

本文档说明如何把本地 PetTrip Agent Service 通过临时公网 HTTPS
入口提供给另一台 Windows 设备，并生成可导入仓库的脱敏验收报告。
本轮只验收 Agent API，不实现或运行 Unity 主程序。

<!-- prettier-ignore -->
> [!IMPORTANT]
> PetTrip Pilot API Key 只适用于内部联调。固定 Key 放入 Unity Player 后仍可能被提取，
> 不能作为正式玩家鉴权方案。

## 验收边界

本轮必须在另一台非服务端设备上完成以下操作：

1. 验证缺失 Key 和错误 Key 返回 `AUTHENTICATION_FAILED`。
2. 使用正确 Key 创建 Session。
3. 上传程序生成的真实 PNG。
4. 使用返回的 `file_id` 创建 Vision Run。
5. 轮询 Run 到 `succeeded`，并验证图片内容。
6. 验证缺少幂等 Key、资源不存在和未鉴权下载的稳定错误码。
7. 鉴权下载上传图片，并在远程设备比较 SHA-256。

本轮不生成 `unity-connectivity-report.json`，也不声明 Unity 跨网络演示完成。
后续 Unity 主程序可以使用同一 PetTrip Pilot API Key 继续联调，但必须单独保存并
验收 Unity 设备结果。

## 服务端准备

服务端使用仓库外的稳定 Key 文件和会话 7 独立数据库。服务启动器强制监听本机
回环地址，并拒绝非 HTTPS Chat Provider。

1. 设置已有 Provider 配置文件路径：

   ```powershell
   $env:PETTRIP_LOCAL_ENV_PATH = '<local-env-file>'
   ```

2. 可选：指定仓库外的 Key 文件。未设置时，启动器使用当前 Windows 用户的
   Local AppData 目录：

   ```powershell
   $env:PETTRIP_PILOT_KEY_PATH = '<private-key-file>'
   ```

3. 启动服务：

   ```powershell
   python -m pilot4mvp2.scripts.run_session7_server
   ```

4. 在另一个终端确认实际监听地址只有 `127.0.0.1`。不要把命令输出直接保存到
   仓库证据，因为它包含本地端口和进程信息。

5. 如果结果包含 `0.0.0.0`、`::`、局域网地址或公网地址，立即停止验收。

完整 PetTrip Pilot API Key 不得出现在命令行、控制台截图、聊天、Shell 历史或
证据中。服务端 SQLite 只保存 Key 的 SHA-256。

## 启动 HTTPS 隧道

本轮使用官方 `cloudflared` Quick Tunnel。隧道只建立出站连接，不要求把本地
Agent Service 端口加入防火墙入站规则。

1. 使用会话 7 启动器创建指向本机回环服务的临时隧道：

   ```powershell
   python -m pilot4mvp2.scripts.run_session7_tunnel
   ```

2. 启动器不回显 `cloudflared` 原始输出。完整 HTTPS Base URL 只写入仓库外受保护的
   `public-base-url.local` 文件。

3. 不要把完整 Base URL、隧道日志或本地 origin 写入仓库。远程报告只保存 Base
   URL 的 SHA-256，正式证据只保存 `<redacted>` 占位符。

4. 在远程验收和证据导入结束后停止隧道；启动器会删除私密 URL 文件。

Quick Tunnel 是短时开发入口，没有稳定域名或可用性承诺。需要长期联调时，改用
Cloudflare Named Tunnel，并在仓库外保存 tunnel credential 和 ingress 配置。

## 准备远程设备

远程设备只需要 Windows PowerShell 5.1 或 PowerShell 7。它不需要 Python、Unity
或本仓库的其余内容。

通过受控文件传输把以下三个文件送到远程设备：

- `remote_client/session7_remote_acceptance.ps1`；
- PetTrip Pilot API Key 文件；
- 仓库外生成的 `public-base-url.local` 文件。

不要把 Key 或完整 Base URL 粘贴到命令行。两个私密配置文件都必须只包含一行，
并在验收后从远程设备删除。

## 执行远程验收

在另一台非服务端 Windows 设备上运行以下命令。建议使用不同宽带、手机热点或蜂窝
网络，以排除只在服务端局域网内可用的假阳性。

```powershell
$parameters = @{
    BaseUrlPath           = '<private-base-url-file>'
    ApiKeyPath            = '<private-key-file>'
    OutputPath            = '<remote-report-file>'
    ConfirmExternalDevice = $true
}

./session7_remote_acceptance.ps1 @parameters
```

脚本使用系统 CA 验证 TLS，关闭自动重定向，不接受跳过证书校验的选项。脚本成功时只
生成白名单 JSON，不保存完整入口、Key、Authorization Header、原图或原始响应。
失败时只输出阶段和异常类型，不输出敏感请求内容。

把生成的脱敏 JSON 报告传回服务端。不要传回 Key 文件、Shell 历史、完整控制台日志
或临时图片。

## 导入证据

服务端必须先人工确认 Agent Service 实际只监听本机回环地址，再导入远程报告：

```powershell
$env:PETTRIP_LOCAL_ENV_PATH = '<local-env-file>'
$env:PETTRIP_PILOT_KEY_PATH = '<private-key-file>'

python -m pilot4mvp2.scripts.verify_session7 `
    --report '<remote-report-file>' `
    --base-url-file '<private-base-url-file>' `
    --origin-loopback-confirmed
```

导入器严格验证报告结构和所有验收结论，只在全部通过时原子发布
`runs/pilot-cross-network-001/`。如果目录已存在，导入器拒绝覆盖。

## 验收后清理

远程报告成功导入后，按以下顺序清理：

1. 停止 `cloudflared`。
2. 停止 Agent Service。
3. 删除远程设备上的 Key 文件、脚本副本和临时报告副本。
4. 清理剪贴板中可能残留的 Base URL 或 Key。
5. 保留服务端仓库外的 PetTrip Pilot API Key，供后续受控 Unity 联调使用。
6. 如果怀疑 Key 泄漏，删除仓库外 Key 文件并重新启动会话 7 服务以生成新 Key。

## 通过声明

正式证据通过后，可以声明：

> 另一台非服务端设备可以通过公网 HTTPS 和 PetTrip Pilot API Key 消费 Agent
> Service，完成鉴权、Session、真实图片上传、异步 Vision Run 轮询、稳定错误响应、
> 鉴权文件下载和 SHA-256 校验。

本轮不能声明：

> Unity 主程序或目标 Unity 设备已经完成跨网络主链路。
