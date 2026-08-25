# Agent Service API 测试报告（2026-08-25）

## 文档说明

本文档记录了对部署的 PetTrip Agent Service 进行的完整 API 测试，包括测试方法、API 使用方式和测试结果。测试目的是验证 Unity MVP API 集成流程的端到端可用性。

**测试时间**：2026-08-25  
**测试环境**：Windows PowerShell 5.1  
**服务地址**：`http://<SERVER_IP>:8001`（实际地址见内部文档）  
**API 认证**：`Authorization: Bearer <API_KEY>`（实际 Key 见内部文档）

---

## 一、测试的 API 端点

本次测试覆盖了以下 API 端点：

### 1.1 健康检查
- **端点**：`GET /health`
- **用途**：验证服务是否正常运行
- **认证**：不需要

### 1.2 Session 管理
- **端点**：`POST /api/v1/sessions`
- **用途**：创建新的对话会话
- **认证**：需要 Bearer Token

### 1.3 澄清输入提交
- **端点**：`POST /api/v1/runs`
- **用途**：提交用户的澄清输入，触发 Agent 处理
- **认证**：需要 Bearer Token + Idempotency-Key

### 1.4 Destination 查询
- **端点**：`GET /api/v1/destinations/{destination_id}`
- **用途**：查询目的地生成进度和结果
- **认证**：需要 Bearer Token

---

## 二、API 使用方法

### 2.1 健康检查

```powershell
$headers = @{
    "Authorization" = "Bearer <API_KEY>"
}

Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/health" -Method Get -Headers $headers
```

**预期响应**：
```json
{
  "status": "ok",
  "service_version": "0.1.0",
  "request_id": "req_..."
}
```

### 2.2 创建 Session

```powershell
$headers = @{
    "Authorization" = "Bearer <API_KEY>"
    "Content-Type" = "application/json; charset=utf-8"
}

$session = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/sessions" -Method Post -Headers $headers
$sessionId = $session.session_id
```

**预期响应**：
```json
{
  "session_id": "session_01...",
  "created_at": "2026-08-25T12:00:00Z",
  "request_id": "req_..."
}
```

### 2.3 提交澄清输入

**关键要点**：
1. 必须使用 UTF-8 编码发送中文内容
2. 每次请求需要唯一的 `Idempotency-Key`
3. 使用 `command` 对象结构，类型为 `clarification.submit_input`

```powershell
$headers = @{
    "Authorization" = "Bearer <API_KEY>"
    "Content-Type" = "application/json; charset=utf-8"
    "Idempotency-Key" = "test-20260825-001"
}

$json = @"
{
  "session_id": "$sessionId",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "test-round-001",
    "text": "我想去一个海边的度假胜地"
  }
}
"@

# 关键：必须显式转换为 UTF-8 字节数组
$body = [Text.Encoding]::UTF8.GetBytes($json)

$response = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/runs" -Method Post -Headers $headers -Body $body
```

**预期响应**：
```json
{
  "run_id": "run_01...",
  "session_id": "session_01...",
  "status": "succeeded",
  "request_id": "req_...",
  "output": {
    "structured_data": {
      "classification": "accepted_wish_input",
      "normalized_text": "...",
      "clarification_closed": false,
      "destination_id": null,
      "close_reason": null
    }
  }
}
```

### 2.4 三轮澄清流程

推荐的三轮输入示例：

**第一轮**：总体需求
```json
{
  "session_id": "session_01...",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "test-round-001",
    "text": "我想去一个海边的度假胜地"
  }
}
```

**第二轮**：具体细节
```json
{
  "session_id": "session_01...",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "test-round-002",
    "text": "希望有椰树、白沙滩和清澈的海水"
  }
}
```

**第三轮**：补充要求
```json
{
  "session_id": "session_01...",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "test-round-003",
    "text": "想要一个安静放松的环境，适合看日落"
  }
}
```

**第三轮响应**会包含：
```json
{
  "output": {
    "structured_data": {
      "classification": "accepted_wish_input",
      "clarification_closed": true,
      "close_reason": "accepted_wish_limit",
      "destination_id": "dest_01..."
    }
  }
}
```

### 2.5 查询 Destination 状态

```powershell
$headers = @{
    "Authorization" = "Bearer <API_KEY>"
}

$destId = "dest_01..."
$dest = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/destinations/$destId" -Method Get -Headers $headers
```

**响应示例**：
```json
{
  "destination_id": "dest_01...",
  "phase": "specification",
  "done": false,
  "terminal_outcome": null,
  "publish_eligible": 0,
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
  "scene_artifacts": []
}
```

### 2.6 轮询 Destination 直到完成

```powershell
$maxPolls = 60
$pollInterval = 10

for ($i = 1; $i -le $maxPolls; $i++) {
    Start-Sleep -Seconds $pollInterval
    
    $dest = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/destinations/$destId" -Method Get -Headers $headers
    
    Write-Host "Poll $i | Phase: $($dest.phase) | Done: $($dest.done)"
    
    if ($dest.done -eq $true) {
        Write-Host "Destination completed!"
        break
    }
}
```

---

## 三、测试结果

### 3.1 成功验证的功能

#### ✅ 健康检查
- **状态**：正常
- **版本**：0.1.0
- **结论**：服务运行正常，端口可访问

#### ✅ Session 创建
- **状态**：成功
- **测试 Session**：
  - `session_01M0VM6P8HCGRM342K485NF0XB`
  - `session_01M0VN418X2PRWT2H8PWWZQF75`
- **结论**：Session 创建 API 工作正常

#### ✅ 澄清输入提交
- **状态**：成功
- **测试轮次**：3 轮
- **分类结果**：全部为 `accepted_wish_input`
- **UTF-8 编码**：必须显式使用 `[Text.Encoding]::UTF8.GetBytes()` 转换
- **结论**：
  - API 正确接收和处理中文输入
  - 三轮澄清流程工作正常
  - 第三轮后自动关闭，原因为 `accepted_wish_limit`

#### ✅ Destination 创建
- **状态**：成功
- **测试 Destination**：
  - `dest_01M0TN3A3B50VFPVMP1YT58XTS`（早期测试）
  - `dest_01M0VMAK0GSX16DKT1W4PMS8H6`（第五轮测试）
  - `dest_01M0VN4WJEB54SHJATVVBFJ1MA`（最终测试）
- **结论**：澄清完成后 Destination 正确创建

#### ✅ 阶段推进（部分）
- **clarification → requirements**：成功（约 10-22 秒）
- **requirements → specification**：成功（立即）
- **结论**：Worker 能够处理前两个阶段转换

#### ✅ Scene Plans 生成
- **状态**：成功
- **生成数量**：2 个 scene_plan
- **示例**：
  - Scene 1: `scene_01M0VN722PWFJEJW9Z3AFT0E32`
  - Scene 2: `scene_01M0VN722TF1AYS9RTBMFPJ5RX`
- **结论**：Specification 阶段成功生成 scene_plans

### 3.2 发现的问题

#### ❌ Destination 卡在 specification 阶段

**问题描述**：
- Destination 进入 `specification` 阶段后无法继续推进
- `scene_plans` 已生成，但 `scene_artifacts` 始终为空
- 无法进入 `generation` 阶段
- 持续时间：超过 9 分钟仍未完成

**测试数据**：
```
Destination: dest_01M0VN4WJEB54SHJATVVBFJ1MA
Phase: specification
Done: false
Scene Plans: 2 (已生成)
Scene Artifacts: 0 (空)
```

**阶段时间线**：
```
13:09:54 - clarification (Destination 创建)
13:10:05 - requirements (约 11 秒)
13:10:16 - specification (约 11 秒)
13:10:27 ~ 13:19:52 - specification (持续 9+ 分钟，未推进)
```

**影响范围**：
- 无法验证完整的端到端流程
- Unity 客户端无法获取最终的 scene_artifacts
- 图片生成和下载功能无法测试

#### ⚠️ UTF-8 响应乱码

**问题描述**：
- 服务器返回的 `normalized_text` 显示为乱码
- 示例：`"æ³å»ææµ·æ»©çå°æ¹åº¦å"` (应为 "我想去有海滩的地方度假")

**影响**：
- 不影响功能，但影响调试和日志可读性
- Unity 客户端可能需要额外处理响应编码

#### ⚠️ 数据持久化问题

**问题描述**：
- 早期测试创建的 Session 和 Destination 在后续查询时返回 `RESOURCE_NOT_FOUND`
- 数据库可能在服务重启后丢失数据

**影响**：
- 无法回溯历史测试数据
- 测试过程中需要每次创建新 Session

### 3.3 完整测试时间线

#### 测试一（最早）
- Session: `session_01M0TN04H3MY83KNAA4CVMMTBJ`
- Destination: `dest_01M0TN3A3B50VFPVMP1YT58XTS`
- 结果：卡在 `requirements` 阶段
- 原因：数据库锁冲突（后续已修复）

#### 测试二至四
- 防火墙、认证格式、UTF-8 编码等问题的排查和修复

#### 测试五
- Session: `session_01M0VM6P8HCGRM342K485NF0XB`
- Destination: `dest_01M0VMAK0GSX16DKT1W4PMS8H6`
- 结果：推进到 `specification` 后卡住

#### 测试六（服务端更新后）
- Session: `session_01M0VN418X2PRWT2H8PWWZQF75`
- Destination: `dest_01M0VN4WJEB54SHJATVVBFJ1MA`
- 结果：仍然卡在 `specification` 阶段
- 持续时间：9+ 分钟

---

## 四、结论与建议

### 4.1 API 层面

**已验证可用**：
- ✅ 健康检查
- ✅ Session 管理
- ✅ 澄清输入提交（含中文 UTF-8）
- ✅ Destination 创建
- ✅ Destination 状态查询
- ✅ 阶段推进（clarification → requirements → specification）
- ✅ Scene Plans 生成

**Unity 客户端集成就绪**：
- 前端 API 调用流程完整可用
- 轮询机制可以正常工作
- 错误处理和重试机制可以实现

### 4.2 后台处理问题

**阻塞点**：`specification` → `generation` 转换失败

**需要服务端排查**：
1. Worker 日志中 specification 阶段的具体错误
2. 是否有未捕获的异常导致 Worker 静默失败
3. 数据库锁或并发问题是否仍然存在
4. Scene generation 调用是否成功触发
5. 图片生成服务（relay_async_image.py）是否正常运行

### 4.3 下一步行动

#### 优先级 P0（阻塞）
- [ ] 修复 specification 阶段卡死问题
- [ ] 验证完整流程可以到达 `done=true`
- [ ] 测试 scene_artifacts 下载

#### 优先级 P1（重要）
- [ ] 修复响应 UTF-8 乱码问题
- [ ] 验证数据持久化稳定性
- [ ] 添加 Worker 超时和错误重试机制

#### 优先级 P2（改进）
- [ ] 优化各阶段处理时间
- [ ] 添加更详细的错误信息返回
- [ ] 完善 API 文档中的错误码说明

---

## 五、附录：完整测试脚本

### 5.1 完整端到端测试脚本

```powershell
# Agent Service 完整测试脚本
# 测试环境：http://<SERVER_IP>:8001

$headers = @{
    "Authorization" = "Bearer <API_KEY>"
    "Content-Type" = "application/json; charset=utf-8"
}

# 1. 健康检查
Write-Host "=== 1. Health Check ===" -ForegroundColor Green
$health = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/health" -Method Get -Headers $headers
Write-Host "Status: $($health.status), Version: $($health.service_version)"

# 2. 创建 Session
Write-Host "`n=== 2. Create Session ===" -ForegroundColor Green
$session = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/sessions" -Method Post -Headers $headers
$sessionId = $session.session_id
Write-Host "Session ID: $sessionId"

# 3. 提交三轮澄清输入
Write-Host "`n=== 3. Submit Clarification Inputs ===" -ForegroundColor Green

# Round 1
$headers["Idempotency-Key"] = "test-$(Get-Date -Format 'yyyyMMddHHmmss')-001"
$json1 = @"
{
  "session_id": "$sessionId",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "test-round-001",
    "text": "我想去一个海边的度假胜地"
  }
}
"@
$body1 = [Text.Encoding]::UTF8.GetBytes($json1)
$r1 = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/runs" -Method Post -Headers $headers -Body $body1
Write-Host "Round 1: $($r1.output.structured_data.classification)"

Start-Sleep -Seconds 1

# Round 2
$headers["Idempotency-Key"] = "test-$(Get-Date -Format 'yyyyMMddHHmmss')-002"
$json2 = @"
{
  "session_id": "$sessionId",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "test-round-002",
    "text": "希望有椰树、白沙滩和清澈的海水"
  }
}
"@
$body2 = [Text.Encoding]::UTF8.GetBytes($json2)
$r2 = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/runs" -Method Post -Headers $headers -Body $body2
Write-Host "Round 2: $($r2.output.structured_data.classification)"

Start-Sleep -Seconds 1

# Round 3
$headers["Idempotency-Key"] = "test-$(Get-Date -Format 'yyyyMMddHHmmss')-003"
$json3 = @"
{
  "session_id": "$sessionId",
  "command": {
    "type": "clarification.submit_input",
    "input_id": "test-round-003",
    "text": "想要一个安静放松的环境，适合看日落"
  }
}
"@
$body3 = [Text.Encoding]::UTF8.GetBytes($json3)
$r3 = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/runs" -Method Post -Headers $headers -Body $body3
Write-Host "Round 3: $($r3.output.structured_data.classification)"
Write-Host "Closed: $($r3.output.structured_data.clarification_closed)"
Write-Host "Destination ID: $($r3.output.structured_data.destination_id)"

$destId = $r3.output.structured_data.destination_id

# 4. 轮询 Destination
Write-Host "`n=== 4. Monitor Destination ===" -ForegroundColor Green
$headers.Remove("Idempotency-Key")

for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 10
    $dest = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/api/v1/destinations/$destId" -Method Get -Headers $headers
    Write-Host "Poll $i | Phase: $($dest.phase) | Done: $($dest.done) | Artifacts: $($dest.scene_artifacts.Count)"
    
    if ($dest.done -eq $true) {
        Write-Host "`nDestination completed!" -ForegroundColor Green
        $dest | ConvertTo-Json -Depth 10
        break
    }
}
```

### 5.2 快速健康检查脚本

```powershell
# 快速检查服务是否正常
$headers = @{ "Authorization" = "Bearer <API_KEY>" }

Write-Host "Health:" -NoNewline
try {
    $h = Invoke-RestMethod -Uri "http://<SERVER_IP>:8001/health" -Method Get -Headers $headers
    Write-Host " OK ($($h.service_version))" -ForegroundColor Green
} catch {
    Write-Host " FAILED" -ForegroundColor Red
}
```

---

## 六、参考文档

- Unity MVP API 文档：`agent_service/docs/agent_service/unity-mvp-api.md`
- Agent Service 架构：`agent_service/README.md`
- Pilot Worktree 工作流：`.claude/projects/.../memory/pilot-worktree-workflow.md`

---

**测试人员**：Claude (Background Agent)  
**文档创建时间**：2026-08-25 13:20  
**最后更新**：2026-08-25 13:20
