[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrlPath,

    [Parameter(Mandatory = $true)]
    [string]$ApiKeyPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [Parameter(Mandatory = $true)]
    [switch]$ConfirmExternalDevice
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.Drawing

function Protect-PrivateFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $nativeArgs = @(
        $Path
        '/inheritance:r'
        '/grant:r'
        ("{0}:F" -f [System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
        '/grant:r'
        'SYSTEM:F'
    )
    & icacls.exe @nativeArgs *> $null
    if ($LASTEXITCODE -ne 0) {
        throw '无法保护私密配置文件。'
    }
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-NormalizedFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $text = [System.IO.File]::ReadAllText($Path).Replace("`r`n", "`n")
    return Get-Sha256Hex -Bytes ([System.Text.Encoding]::UTF8.GetBytes($text))
}

function New-TestPng {
    $bitmap = [System.Drawing.Bitmap]::new(128, 64)
    $stream = [System.IO.MemoryStream]::new()
    try {
        $red = [System.Drawing.Color]::FromArgb(255, 0, 0)
        $blue = [System.Drawing.Color]::FromArgb(0, 0, 255)
        for ($x = 0; $x -lt 128; $x++) {
            $color = if ($x -lt 64) { $red } else { $blue }
            for ($y = 0; $y -lt 64; $y++) {
                $bitmap.SetPixel($x, $y, $color)
            }
        }
        $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        return $stream.ToArray()
    }
    finally {
        $stream.Dispose()
        $bitmap.Dispose()
    }
}

function New-RequestUri {
    param(
        [Parameter(Mandatory = $true)][Uri]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not $Path.StartsWith('/')) {
        throw 'API 路径必须是根相对路径。'
    }
    return [Uri]::new($Root, $Path.TrimStart('/'))
}

function Invoke-ApiRequest {
    param(
        [Parameter(Mandatory = $true)][System.Net.Http.HttpClient]$Client,
        [Parameter(Mandatory = $true)][Uri]$Root,
        [Parameter(Mandatory = $true)][System.Net.Http.HttpMethod]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowNull()][string]$ApiKey,
        [AllowNull()][System.Net.Http.HttpContent]$Content,
        [AllowNull()][string]$IdempotencyKey,
        [switch]$ReadBytes
    )

    $request = [System.Net.Http.HttpRequestMessage]::new($Method, (New-RequestUri -Root $Root -Path $Path))
    try {
        if (-not [string]::IsNullOrWhiteSpace($ApiKey)) {
            $request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $ApiKey)
        }
        if (-not [string]::IsNullOrWhiteSpace($IdempotencyKey)) {
            $null = $request.Headers.TryAddWithoutValidation('Idempotency-Key', $IdempotencyKey)
        }
        if ($null -ne $Content) {
            $request.Content = $Content
        }

        $response = $Client.SendAsync($request).GetAwaiter().GetResult()
        try {
            $statusCode = [int]$response.StatusCode
            if ($ReadBytes) {
                return [pscustomobject]@{
                    StatusCode = $statusCode
                    Bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
                    Json = $null
                }
            }

            $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            $json = $null
            if (-not [string]::IsNullOrWhiteSpace($text)) {
                try {
                    $json = $text | ConvertFrom-Json
                }
                catch {
                    throw "API 返回的状态 $statusCode 不是 JSON。"
                }
            }
            return [pscustomobject]@{
                StatusCode = $statusCode
                Bytes = $null
                Json = $json
            }
        }
        finally {
            $response.Dispose()
        }
    }
    finally {
        $request.Dispose()
    }
}

function New-JsonContent {
    param([Parameter(Mandatory = $true)][object]$Value)

    $json = $Value | ConvertTo-Json -Depth 12 -Compress
    return [System.Net.Http.StringContent]::new($json, [System.Text.Encoding]::UTF8, 'application/json')
}

function Assert-ErrorCase {
    param(
        [Parameter(Mandatory = $true)][object]$Response,
        [Parameter(Mandatory = $true)][int]$ExpectedStatus,
        [Parameter(Mandatory = $true)][string]$ExpectedCode
    )

    if ($Response.StatusCode -ne $ExpectedStatus -or $null -eq $Response.Json) {
        throw '错误响应的 HTTP 契约不符合预期。'
    }
    if ($Response.Json.error.code -ne $ExpectedCode) {
        throw '错误响应的稳定错误码不符合预期。'
    }
    if ([string]::IsNullOrWhiteSpace([string]$Response.Json.request_id)) {
        throw '错误响应缺少 request_id。'
    }
    return [ordered]@{
        http_status = $Response.StatusCode
        error_code = [string]$Response.Json.error.code
        request_id = [string]$Response.Json.request_id
    }
}

if (-not $ConfirmExternalDevice.IsPresent) {
    throw '必须显式确认本脚本运行在非服务端设备上。'
}
if (Test-Path -LiteralPath $OutputPath) {
    throw '输出报告已存在，拒绝覆盖。'
}
if (-not (Test-Path -LiteralPath $ApiKeyPath -PathType Leaf)) {
    throw 'PetTrip Pilot API Key 文件不存在。'
}
if (-not (Test-Path -LiteralPath $BaseUrlPath -PathType Leaf)) {
    throw '公网 HTTPS Base URL 文件不存在。'
}

Protect-PrivateFile -Path (Resolve-Path -LiteralPath $BaseUrlPath).Path
Protect-PrivateFile -Path (Resolve-Path -LiteralPath $ApiKeyPath).Path
$baseUrl = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $BaseUrlPath)).Trim()
$root = [Uri]::new($baseUrl.TrimEnd('/') + '/')
$baseUrl = $null
$trustedHost = $root.DnsSafeHost -match '^[a-z0-9-]+\.trycloudflare\.com$'
if ($root.Scheme -ne 'https' -or -not $root.IsDefaultPort -or -not $trustedHost -or $root.AbsolutePath -ne '/' -or -not [string]::IsNullOrEmpty($root.UserInfo) -or -not [string]::IsNullOrEmpty($root.Query) -or -not [string]::IsNullOrEmpty($root.Fragment)) {
    throw 'Base URL 必须是受信任的 Cloudflare Quick Tunnel HTTPS 根地址。'
}
$pilotKey = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $ApiKeyPath)).Trim()
if (-not $pilotKey.StartsWith('pettrip_pilot_') -or $pilotKey.Length -lt 48 -or $pilotKey -match '\s') {
    throw 'PetTrip Pilot API Key 格式不安全。'
}

[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(30)
$stage = 'initialization'

try {
    $stage = 'health'
    $health = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Get) -Path '/health' -ApiKey $null -Content $null -IdempotencyKey $null
    if ($health.StatusCode -ne 200 -or $health.Json.status -ne 'ok') {
        throw '公网健康检查失败。'
    }

    $stage = 'authentication-negative-cases'
    $missingKeyResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Post) -Path '/api/v1/sessions' -ApiKey $null -Content $null -IdempotencyKey $null
    $wrongKey = 'wrong_' + [Guid]::NewGuid().ToString('N')
    $wrongKeyResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Post) -Path '/api/v1/sessions' -ApiKey $wrongKey -Content $null -IdempotencyKey $null
    $missingKeyCase = Assert-ErrorCase -Response $missingKeyResponse -ExpectedStatus 401 -ExpectedCode 'AUTHENTICATION_FAILED'
    $wrongKeyCase = Assert-ErrorCase -Response $wrongKeyResponse -ExpectedStatus 401 -ExpectedCode 'AUTHENTICATION_FAILED'

    $stage = 'session'
    $sessionResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Post) -Path '/api/v1/sessions' -ApiKey $pilotKey -Content $null -IdempotencyKey $null
    if ($sessionResponse.StatusCode -ne 201 -or [string]::IsNullOrWhiteSpace([string]$sessionResponse.Json.session_id)) {
        throw 'Session 创建失败。'
    }
    $sessionId = [string]$sessionResponse.Json.session_id

    $stage = 'upload'
    $imageBytes = New-TestPng
    $sourceHash = Get-Sha256Hex -Bytes $imageBytes
    $multipart = [System.Net.Http.MultipartFormDataContent]::new()
    $fileContent = [System.Net.Http.ByteArrayContent]::new($imageBytes)
    $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new('image/png')
    $multipart.Add($fileContent, 'file', 'left-red-right-blue.png')
    $multipart.Add([System.Net.Http.StringContent]::new('vision_input'), 'purpose')
    $uploadResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Post) -Path '/api/v1/files' -ApiKey $pilotKey -Content $multipart -IdempotencyKey $null
    if ($uploadResponse.StatusCode -ne 201) {
        throw '图片上传失败。'
    }
    $upload = $uploadResponse.Json
    if ($upload.source -ne 'user_upload' -or $upload.purpose -ne 'vision_input' -or $upload.mime_type -ne 'image/png' -or [int]$upload.width -ne 128 -or [int]$upload.height -ne 64 -or [int]$upload.size_bytes -ne $imageBytes.Length -or $upload.sha256 -ne $sourceHash) {
        throw '图片上传元数据不符合预期。'
    }
    $fileId = [string]$upload.file_id

    $stage = 'run-error-cases'
    $question = '图片左半边和右半边分别是什么颜色？只输出 JSON 对象，键必须是 left 和 right，值使用英文颜色名；不要输出其他内容。'
    $runBody = [ordered]@{
        session_id = $sessionId
        input = [ordered]@{
            text = $question
            attachments = @([ordered]@{ file_id = $fileId; purpose = 'vision_input' })
        }
        response_format = [ordered]@{ modalities = @('text') }
    }
    $missingIdempotencyResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Post) -Path '/api/v1/runs' -ApiKey $pilotKey -Content (New-JsonContent -Value $runBody) -IdempotencyKey $null
    $missingIdempotencyCase = Assert-ErrorCase -Response $missingIdempotencyResponse -ExpectedStatus 400 -ExpectedCode 'VALIDATION_ERROR'
    $missingResourceResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Get) -Path '/api/v1/runs/run_missing' -ApiKey $pilotKey -Content $null -IdempotencyKey $null
    $missingResourceCase = Assert-ErrorCase -Response $missingResourceResponse -ExpectedStatus 404 -ExpectedCode 'RESOURCE_NOT_FOUND'
    $unauthorizedDownloadResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Get) -Path ("/api/v1/files/{0}/content" -f $fileId) -ApiKey $null -Content $null -IdempotencyKey $null
    $unauthorizedDownloadCase = Assert-ErrorCase -Response $unauthorizedDownloadResponse -ExpectedStatus 401 -ExpectedCode 'AUTHENTICATION_FAILED'

    $stage = 'run-create'
    $idempotencyKey = 'session7-' + [Guid]::NewGuid().ToString('N')
    $runResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Post) -Path '/api/v1/runs' -ApiKey $pilotKey -Content (New-JsonContent -Value $runBody) -IdempotencyKey $idempotencyKey
    if ($runResponse.StatusCode -ne 202 -or [string]::IsNullOrWhiteSpace([string]$runResponse.Json.run_id)) {
        throw 'Run 创建失败。'
    }
    $runId = [string]$runResponse.Json.run_id
    $statuses = [System.Collections.Generic.List[string]]::new()
    $statuses.Add([string]$runResponse.Json.status)

    $stage = 'run-poll'
    $terminal = $null
    $deadline = [DateTime]::UtcNow.AddSeconds(180)
    while ([DateTime]::UtcNow -lt $deadline) {
        $pollResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Get) -Path ("/api/v1/runs/{0}" -f $runId) -ApiKey $pilotKey -Content $null -IdempotencyKey $null
        if ($pollResponse.StatusCode -ne 200) {
            throw 'Run 轮询失败。'
        }
        $status = [string]$pollResponse.Json.status
        if ($status -notin @('queued', 'running', 'succeeded', 'failed')) {
            throw 'Run 返回未知状态。'
        }
        if ($statuses[$statuses.Count - 1] -ne $status) {
            $statuses.Add($status)
        }
        if ($status -in @('succeeded', 'failed')) {
            $terminal = $pollResponse.Json
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if ($null -eq $terminal -or $terminal.status -ne 'succeeded') {
        throw 'Run 未在时限内成功。'
    }

    $assistantText = [string]$terminal.output.text
    try {
        $vision = $assistantText | ConvertFrom-Json
    }
    catch {
        throw 'Vision 文本不是预期 JSON。'
    }
    $left = ([string]$vision.left).ToLowerInvariant()
    $right = ([string]$vision.right).ToLowerInvariant()
    if ($left -ne 'red' -or $right -ne 'blue') {
        throw 'Vision 结果与测试图片不一致。'
    }

    $stage = 'download'
    $downloadResponse = Invoke-ApiRequest -Client $client -Root $root -Method ([System.Net.Http.HttpMethod]::Get) -Path ("/api/v1/files/{0}/content" -f $fileId) -ApiKey $pilotKey -Content $null -IdempotencyKey $null -ReadBytes
    if ($downloadResponse.StatusCode -ne 200) {
        throw '鉴权文件下载失败。'
    }
    $downloadHash = Get-Sha256Hex -Bytes $downloadResponse.Bytes
    if ($downloadHash -ne $sourceHash -or $downloadHash -ne [string]$upload.sha256) {
        throw '远程文件下载哈希不一致。'
    }

    $stage = 'report'
    $canonicalBaseUrl = $root.AbsoluteUri.TrimEnd('/')
    $baseUrlHash = Get-Sha256Hex -Bytes ([System.Text.Encoding]::UTF8.GetBytes($canonicalBaseUrl))
    $report = [ordered]@{
        schema_version = '1.2'
        session = 7
        scope = 'remote_agent_api'
        producer = 'pettrip_session7_powershell_client'
        operator_attested_external_device = $true
        executed_at_utc = [DateTime]::UtcNow.ToString('o')
        remote_client_sha256 = Get-NormalizedFileSha256 -Path $PSCommandPath
        public_transport = [ordered]@{
            scheme = 'https'
            base_url_sha256 = $baseUrlHash
            tls_validation_enabled = $true
            redirects_followed = $false
        }
        authentication = [ordered]@{
            missing_key = $missingKeyCase
            wrong_key = $wrongKeyCase
        }
        session_request = [ordered]@{
            http_status = $sessionResponse.StatusCode
            session_id = $sessionId
            request_id = [string]$sessionResponse.Json.request_id
        }
        upload = [ordered]@{
            http_status = $uploadResponse.StatusCode
            file_id = $fileId
            request_id = [string]$upload.request_id
            source = [string]$upload.source
            purpose = [string]$upload.purpose
            mime_type = [string]$upload.mime_type
            width = [int]$upload.width
            height = [int]$upload.height
            size_bytes = [int]$upload.size_bytes
            sha256 = [string]$upload.sha256
        }
        run = [ordered]@{
            create_http_status = $runResponse.StatusCode
            run_id = $runId
            create_request_id = [string]$runResponse.Json.request_id
            statuses_observed = @($statuses)
            terminal_status = [string]$terminal.status
            terminal_request_id = [string]$terminal.request_id
            vision_answer = [ordered]@{ left = $left; right = $right }
        }
        download = [ordered]@{
            http_status = $downloadResponse.StatusCode
            sha256 = $downloadHash
            matches_source = ($downloadHash -eq $sourceHash)
            matches_metadata = ($downloadHash -eq [string]$upload.sha256)
        }
        errors = [ordered]@{
            missing_idempotency_key = $missingIdempotencyCase
            missing_resource = $missingResourceCase
            unauthorized_download = $unauthorizedDownloadCase
        }
        remote_api_scope_passed = $true
    }

    $outputParent = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($outputParent) -and -not (Test-Path -LiteralPath $outputParent)) {
        $null = New-Item -ItemType Directory -Path $outputParent
    }
    $json = $report | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($OutputPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Write-Output '会话 7 远程 API 验收通过；已生成脱敏 JSON 报告。'
}
catch {
    if (Test-Path -LiteralPath $OutputPath) {
        Remove-Item -LiteralPath $OutputPath -Force -Confirm:$false
    }
    Write-Error ("会话 7 远程 API 验收失败；阶段={0}，错误类型={1}。" -f $stage, $_.Exception.GetType().Name)
    exit 1
}
finally {
    $pilotKey = $null
    $client.Dispose()
    $handler.Dispose()
}
