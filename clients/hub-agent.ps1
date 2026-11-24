# MCP 集线器客户端（PowerShell，零依赖）
#
# 任何能执行命令的智能体都可以用这个脚本接入集线器：注册、心跳、收发消息。
# 会话 id 持久化在 %LOCALAPPDATA%\mcp-hub\，同一 agent_id 后续命令自动复用。
#
# 为避免命令行引号/中文编码问题，复杂参数建议用文件：
#   -SpecFile    注册信息 JSON：{"name":"豆包","topics":["report"],"capabilities":["chat"]}
#   -PayloadFile 消息负载 JSON：{"task":"write-report"}
#
# 常用：
#   powershell -File clients\hub-agent.ps1 register -AgentId doubao -SpecFile doubao.json
#   powershell -File clients\hub-agent.ps1 heartbeat -AgentId doubao
#   powershell -File clients\hub-agent.ps1 send -AgentId doubao -Target minimax -PayloadFile task.json
#   powershell -File clients\hub-agent.ps1 fetch -AgentId minimax
#   powershell -File clients\hub-agent.ps1 poll -AgentId minimax -Interval 3
#   powershell -File clients\hub-agent.ps1 list

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('register', 'heartbeat', 'send', 'fetch', 'list', 'unregister', 'poll', 'whoami')]
    [string]$Command,

    [string]$AgentId = 'agent-ps',
    [string]$Name,
    [string]$Topics = '',
    [string]$Capabilities = '',
    [string]$SpecFile,
    [string]$Status = 'online',
    [string]$Target,
    [string]$Payload = '{}',
    [string]$PayloadFile,
    [string]$Topic,
    [switch]$Broadcast,
    [int]$Limit = 20,
    [string]$Url = 'http://127.0.0.1:8765',
    [int]$Interval = 3,
    [int]$MaxRounds = 0
)

$ErrorActionPreference = 'Stop'
# 统一 UTF-8，避免中文在管道/重定向时乱码
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$Url = $Url.TrimEnd('/')
$SessionDir = Join-Path $env:LOCALAPPDATA 'mcp-hub'
New-Item -ItemType Directory -Force -Path $SessionDir | Out-Null

function Split-List([string]$text) {
    if ([string]::IsNullOrWhiteSpace($text)) { return @() }
    return @($text -split '[,;]' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Read-JsonFile([string]$path) {
    $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    return ($raw | ConvertFrom-Json)
}

function Get-SessionFile([string]$id) {
    $safe = ($id -replace '[^\w\.\-]', '_')
    return (Join-Path $SessionDir "session-$safe.json")
}

function Load-SessionId([string]$id) {
    $file = Get-SessionFile $id
    if (Test-Path $file) { return (Get-Content $file -Raw | ConvertFrom-Json).sessionId }
    return $null
}

function Save-SessionId([string]$id, [string]$sessionId) {
    @{ sessionId = $sessionId; agentId = $id } |
        ConvertTo-Json | Set-Content -Path (Get-SessionFile $id) -Encoding UTF8
}

function Invoke-Mcp([string]$Method, $Params, [string]$SessionId) {
    $body = @{
        jsonrpc = '2.0'
        id      = Get-Random -Minimum 1 -Maximum 1000000
        method  = $Method
        params  = $Params
    } | ConvertTo-Json -Depth 12 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $headers = @{ 'Content-Type' = 'application/json'; 'Accept' = 'application/json' }
    if ($SessionId) { $headers['Mcp-Session-Id'] = $SessionId }

    $resp = Invoke-WebRequest -Uri "$Url/mcp" -Method Post -Headers $headers -Body $bytes `
        -UseBasicParsing -TimeoutSec 20
    $sid = $resp.Headers['Mcp-Session-Id']
    if ($sid -is [array]) { $sid = $sid[0] }

    if ($resp.StatusCode -eq 202 -or [string]::IsNullOrWhiteSpace($resp.RawContentStream.Length)) {
        return @{ Result = $null; SessionId = $sid }
    }
    # 显式按 UTF-8 解码：PowerShell 5.1 在没有 charset 时默认按 ISO-8859-1 处理，中文会乱码
    $rawBytes = $resp.RawContentStream.ToArray()
    $content = [System.Text.Encoding]::UTF8.GetString($rawBytes)
    $parsed = $content | ConvertFrom-Json
    if ($parsed.PSObject.Properties.Name -contains 'error' -and $parsed.error) {
        throw ("JSON-RPC 错误 {0}: {1}" -f $parsed.error.code, $parsed.error.message)
    }
    return @{ Result = $parsed.result; SessionId = $sid }
}

function Initialize-Hub([string]$id) {
    # 带上本地缓存的会话 id 再握手：集线器重启后旧会话失效，会返回一个新的 id
    $existing = Load-SessionId $id
    $r = Invoke-Mcp 'initialize' @{
        protocolVersion = '2025-06-18'
        capabilities    = @{}
        clientInfo      = @{ name = $id; version = '1.0' }
    } $existing
    $sid = if ($r.SessionId) { $r.SessionId } else { $existing }
    if ($sid) { Save-SessionId $id $sid }
    return $sid
}

function Get-SessionOrInit([string]$id) {
    # 每次都握手：幂等、开销小，且能自动修复「集线器重启后旧会话失效」
    return (Initialize-Hub $id)
}

function Invoke-HubTool([string]$session, [string]$tool, [hashtable]$arguments) {
    try {
        $r = Invoke-Mcp 'tools/call' @{ name = $tool; arguments = $arguments } $session
    } catch {
        # 会话可能随集线器重启失效：重新握手后重试一次
        $script:session = Initialize-Hub $AgentId
        $r = Invoke-Mcp 'tools/call' @{ name = $tool; arguments = $arguments } $script:session
    }
    $content = @($r.Result.content)
    $text = if ($content.Count -gt 0) { $content[0].text } else { '{}' }
    if ($r.Result.isError) { throw "工具 $tool 返回错误：$text" }
    return ($text | ConvertFrom-Json)
}

function Show($value) { $value | ConvertTo-Json -Depth 12 }

switch ($Command) {
    'whoami' {
        $session = Get-SessionOrInit $AgentId
        [pscustomobject]@{
            agent_id     = $AgentId
            session_id   = $session
            url          = $Url
            session_file = (Get-SessionFile $AgentId)
        } | ConvertTo-Json
    }
    'register' {
        $session = Get-SessionOrInit $AgentId
        $spec = @{}
        if ($SpecFile) {
            $file = Read-JsonFile $SpecFile
            foreach ($prop in $file.PSObject.Properties) { $spec[$prop.Name] = $prop.Value }
        }
        $displayName = if ($spec['name']) { $spec['name'] } elseif ($Name) { $Name } else { $AgentId }
        $topicList = if ($spec['topics']) { @($spec['topics']) } else { Split-List $Topics }
        $capList = if ($spec['capabilities']) { @($spec['capabilities']) } else { Split-List $Capabilities }
        $metadata = if ($spec['metadata']) { $spec['metadata'] } else {
            @{ client = 'powershell'; host = $env:COMPUTERNAME }
        }
        Show (Invoke-HubTool $session 'hub_register' @{
            agent_id     = $AgentId
            name         = $displayName
            version      = '1.0'
            capabilities = @($capList)
            topics       = @($topicList)
            metadata     = $metadata
        })
    }
    'heartbeat' {
        $session = Get-SessionOrInit $AgentId
        Show (Invoke-HubTool $session 'hub_heartbeat' @{ status = $Status })
    }
    'send' {
        $session = Get-SessionOrInit $AgentId
        if ($PayloadFile) { $data = Read-JsonFile $PayloadFile }
        else { $data = $Payload | ConvertFrom-Json }

        if ($Broadcast) { $to = '*' }
        elseif ($Topic) { $to = "topic:$Topic" }
        elseif ($Target) { $to = $Target }
        else { throw '请指定 -Target 或 -Topic 或 -Broadcast' }

        $arguments = @{ target = $to; payload = $data }
        if ($Topic) { $arguments['topic'] = $Topic }
        Show (Invoke-HubTool $session 'hub_send' $arguments)
    }
    'fetch' {
        $session = Get-SessionOrInit $AgentId
        Show (Invoke-HubTool $session 'hub_fetch_messages' @{ limit = $Limit })
    }
    'list' {
        $session = Get-SessionOrInit $AgentId
        Show (Invoke-HubTool $session 'hub_list_agents' @{ online_only = $false })
    }
    'unregister' {
        $session = Get-SessionOrInit $AgentId
        Show (Invoke-HubTool $session 'hub_unregister' @{ agent_id = $AgentId })
    }
    'poll' {
        $session = Get-SessionOrInit $AgentId
        $round = 0
        while ($true) {
            $inbox = Invoke-HubTool $session 'hub_fetch_messages' @{ limit = $Limit }
            if ($inbox.count -gt 0) {
                foreach ($message in $inbox.messages) {
                    Write-Output ('MESSAGE ' + ($message | ConvertTo-Json -Depth 12 -Compress))
                }
            }
            $round++
            if ($MaxRounds -gt 0 -and $round -ge $MaxRounds) { break }
            Start-Sleep -Seconds $Interval
        }
    }
}
