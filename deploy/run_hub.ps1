# MCP 消息集线器启动脚本（供计划任务 / 手动启动使用）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File deploy\run_hub.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv\Scripts\python.exe'
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'hub.log'

if (-not (Test-Path $python)) {
    "[$(Get-Date -Format s)] 找不到 $python，请先创建 .venv 并 pip install -e ." |
        Out-File -FilePath $logFile -Append -Encoding utf8
    exit 1
}

# 已有实例在监听则直接退出，避免端口冲突
$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    "[$(Get-Date -Format s)] 8765 已被占用，跳过启动" | Out-File -FilePath $logFile -Append -Encoding utf8
    exit 0
}

$env:PYTHONIOENCODING = 'utf-8'
Set-Location $root
"[$(Get-Date -Format s)] 启动 MCP Hub (pid=$PID)" | Out-File -FilePath $logFile -Append -Encoding utf8

# 前台运行；由计划任务负责常驻，进程退出后事件日志里可查
& $python -m mcp_hub --host 127.0.0.1 --port 8765 --log-level INFO *>&1 |
    Tee-Object -FilePath $logFile -Append
