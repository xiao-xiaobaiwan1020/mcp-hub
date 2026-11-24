# 让 MCP Hub 在登录时自动启动（当前用户，免管理员）
#
# 原理：在「启动」文件夹放一个隐藏窗口的快捷方式，登录 Windows 后自动运行
#       deploy\run_hub.ps1。比计划任务更省事，且不需要管理员权限。
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File deploy\install_autostart.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent
$script = Join-Path $root 'deploy\run_hub.ps1'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'MCP Hub.lnk'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

# 1) 写入启动快捷方式
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $ps
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7          # 最小化，避免闪窗
$shortcut.Description = 'MCP 消息集线器（登录自启）'
$shortcut.Save()
Write-Host "已创建启动项：$lnkPath"

# 2) 立即后台启动一次
Start-Process -FilePath $ps -ArgumentList $arguments -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 5

# 3) 健康检查
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/admin/health' -TimeoutSec 5
    Write-Host ("健康检查通过：{0}" -f ($health | ConvertTo-Json -Compress))
    Write-Host "访问地址：http://127.0.0.1:8765/mcp（MCP 端点）、http://127.0.0.1:8765/admin/agents（在线列表）"
} catch {
    Write-Warning "集线器尚未就绪：$($_.Exception.Message)"
    Write-Warning "查看日志：$root\logs\hub.log"
}

Write-Host '完成。卸载：deploy\uninstall_autostart.ps1'
