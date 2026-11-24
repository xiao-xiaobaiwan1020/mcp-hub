# 移除 MCP Hub 的登录自启（并尝试停止正在运行的实例）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File deploy\uninstall_autostart.ps1
$ErrorActionPreference = 'Continue'

$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'MCP Hub.lnk'
if (Test-Path $lnkPath) {
    Remove-Item $lnkPath -Force
    Write-Host "已删除启动项：$lnkPath"
} else {
    Write-Host '启动项不存在'
}

# 停止本仓库路径下的 mcp_hub 进程（按命令行匹配，避免误杀）
$target = 'mcp_hub'
$processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*mcp_hub*--port*8765*" }
foreach ($process in $processes) {
    Write-Host "停止进程 PID=$($process.ProcessId)"
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

# 兼容旧版计划任务
schtasks /delete /tn 'MCPServer-Hub' /f 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host '已删除旧计划任务 MCPServer-Hub' }
