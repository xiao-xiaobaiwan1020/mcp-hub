# 让其他智能体接入集线器（操作卡）

> 本机集线器地址：`http://127.0.0.1:8765`
> MCP 端点：`http://127.0.0.1:8765/mcp`　在线列表：`http://127.0.0.1:8765/admin/agents`
> 客户端脚本：`D:\软件开发\Harness\mcp-hub\clients\hub-agent.ps1`（PowerShell，零依赖）

集线器在登录 Windows 后自动启动（启动文件夹里的 `MCP Hub.lnk`），不需要手动开。

---

## 一、给「能执行命令」的智能体（MiniMax Code、WorkBuddy 等）

把下面这段整段粘贴给它，让它按规则执行：

```
你现在已接入本机的 MCP 消息集线器（多智能体协作总线），地址是 http://127.0.0.1:8765。

你的智能体 id 是 minimax，显示名 MiniMax Code。

工作规则：
1. 会话开始时先注册（只需一次）：
   powershell -NoProfile -ExecutionPolicy Bypass -File "D:\软件开发\Harness\mcp-hub\clients\hub-agent.ps1" register -AgentId minimax -SpecFile "D:\软件开发\Harness\mcp-hub\clients\examples\minimax.json"

2. 收任务：执行
   powershell -NoProfile -ExecutionPolicy Bypass -File "D:\软件开发\Harness\mcp-hub\clients\hub-agent.ps1" fetch -AgentId minimax
   返回 JSON 里的 messages 数组就是待办任务，每条有 sender / payload。

3. 完成任务后回结果：把结果写成 UTF-8 的 JSON 文件（例如 result.json），再执行
   powershell -NoProfile -ExecutionPolicy Bypass -File "D:\软件开发\Harness\mcp-hub\clients\hub-agent.ps1" send -AgentId minimax -Target <任务的 sender> -PayloadFile result.json

4. 每轮工作前执行一次心跳：
   powershell -NoProfile -ExecutionPolicy Bypass -File "D:\软件开发\Harness\mcp-hub\clients\hub-agent.ps1" heartbeat -AgentId minimax

消息约定：payload.action 使用 report.request / report.result / check.request / check.result / alarm.raised 这类点分命名。
```

**说明**：这类应用不是常驻服务，做不到真的每 10 秒心跳、也不能后台收消息。实际用法是「你在对话里让它执行一条命令」——`fetch` 收任务、`send` 回结果。心跳不做也能收发，只是它会被显示为离线（点对点消息仍会排队等它拉取）。

---

## 二、给「不能执行命令」的智能体（豆包、千问办公等桌面聊天版）

它们无法运行脚本，只能**人工接力**：

1. 在集线器里生成任务卡（把要它做的事 + 数据写清楚）
2. 你粘贴进它的对话框，拿到结果
3. 把结果贴回给我（或写成 JSON，我帮你 `send` 出去）

它在集线器里仍可以有一条记录（`register` 由我代发），这样消息流转能留下痕迹。

---

## 三、常用命令速查

```powershell
$HUB = "D:\软件开发\Harness\mcp-hub\clients\hub-agent.ps1"

# 注册（显示名/主题/能力写在 spec 文件里，避免命令行中文乱码）
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB register -AgentId doubao -SpecFile "clients\examples\doubao.json"

# 心跳
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB heartbeat -AgentId doubao

# 发消息（点对点 / 主题 / 广播三选一）
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB send -AgentId doubao -Target minimax -PayloadFile task.json
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB send -AgentId doubao -Topic report -PayloadFile task.json
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB send -AgentId doubao -Broadcast -PayloadFile task.json

# 收消息（拉取并清空）
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB fetch -AgentId minimax -Limit 20

# 持续轮询（每 3 秒一次，Ctrl+C 退出）
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB poll -AgentId minimax -Interval 3

# 在线列表 / 注销
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB list
powershell -NoProfile -ExecutionPolicy Bypass -File $HUB unregister -AgentId minimax
```

---

## 四、避免踩的三个坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 中文显示成 `è±å` 或 `ç«¹æ` | PowerShell 5.1 默认按 ISO-8859-1 解码响应 | 脚本已修（显式 UTF-8）；命令行里的中文参数仍建议改用 `-SpecFile` / `-PayloadFile` |
| `发送前需要先注册` | 集线器重启后会话失效 | 重新执行一次 `register` 即可（脚本会自动重新握手） |
| `count: 0` 但对方说发了 | 消息被上一次 `fetch` 取走，或目标 id 写错 | `list` 确认 agent_id；消息是「取走即删」 |

---

## 五、运维

```powershell
# 查看在线情况
Invoke-RestMethod http://127.0.0.1:8765/admin/agents | ConvertTo-Json -Depth 5

# 健康检查 / 统计 / Prometheus 指标
Invoke-RestMethod http://127.0.0.1:8765/admin/health
Invoke-RestMethod http://127.0.0.1:8765/admin/stats
Invoke-WebRequest http://127.0.0.1:8765/admin/metrics

# 日志
Get-Content D:\软件开发\Harness\mcp-hub\logs\hub.log -Tail 50

# 停止 / 卸载自启
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\uninstall_autostart.ps1
```
