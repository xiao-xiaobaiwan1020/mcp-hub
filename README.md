# MCP 消息集线器（mcp-hub）

多 Agent 注册、心跳、路由与消息转发的轻量集线器，按 **MCP 标准协议**（JSON-RPC 2.0 / Streamable HTTP）提供服务，纯 **asyncio** 异步实现。目标场景是监测平台里的多智能体协作：报告 Agent、OCR Agent、告警 Agent 各自注册上线，通过集线器互相投递任务与事件。

## 特性

- **MCP 标准协议**：`POST /mcp` 走 JSON-RPC 2.0，支持 `initialize` / `tools/list` / `tools/call` / `ping`；`GET /mcp` 是 SSE 长连接，用于服务端推送。
- **Agent 注册表**：唯一 id、显示名、版本、能力标签、主题订阅、自定义元数据。
- **心跳与离线判定**：Agent 定期 `hub_heartbeat`，超过 `heartbeat_timeout` 未心跳自动置为离线；离线超过保留期自动清理。
- **三种投递**：点对点（`agent_id`）、主题（`topic:xxx`）、广播（`*`）。
- **双通道接收**：有 SSE 连接实时推送；没有连接也能用 `hub_fetch_messages` 拉取（消息先入有界队列，不会丢在半路）。
- **管理接口**：在线 Agent 列表、详情、待投递消息、强制下线、统计、Prometheus 指标。
- **Webhook 事件**：注册 / 上线 / 离线 / 注销 / 发送事件可推送监测平台。
- **可测试**：26 个单测覆盖注册表、路由、MCP 协议与管理接口，全部走 ASGI，无外部依赖。
- **结构清晰**：协议层、业务层、传输层分离，注释与类型标注完整，便于后续扩展。

## 架构

```
 Agent A ──POST /mcp (tools/call)──┐
 Agent B ──POST /mcp───────────────┤        ┌──────────────┐
 Agent C ──GET  /mcp (SSE)◄────────┼───────►│  MCP 协议层   │
                                   │        │  server.py   │
 监测平台 ──GET /admin/*───────────┘        └──────┬───────┘
                                                   │
                        ┌──────────────────────────┼──────────────────────────┐
                        ▼                          ▼                          ▼
                 ┌─────────────┐           ┌─────────────┐            ┌─────────────┐
                 │  AgentRegistry │        │  MessageBus │            │   Router    │
                 │ 注册/心跳/主题 │        │ 队列 + SSE  │            │ 点对点/主题/广播 │
                 └─────────────┘           └─────────────┘            └─────────────┘
                        │                          ▲                          │
                        └────────── Hub（生命周期 / reaper / 指标 / Webhook）──┘
```

## 目录结构

```
mcp_hub/
├── config.py          配置（环境变量 MCP_HUB_*）
├── logging_setup.py   日志（文本 / JSON）
├── models.py          Pydantic 模型：Agent、Envelope、DeliveryReport、统计
├── registry.py        Agent 注册表 + 心跳超时 + 主题订阅
├── bus.py             有界消息总线（待投递队列 + SSE sink）
├── router.py          路由：direct / topic / broadcast
├── metrics.py         指标 + Prometheus 文本
├── events.py          Webhook 事件分发
├── hub.py             服务容器与后台 reaper
├── mcp/
│   ├── protocol.py    JSON-RPC / MCP 常量与编解码
│   ├── session.py     MCP 会话管理
│   ├── tools.py       8 个 hub_* 工具
│   └── server.py      /mcp 的 POST / GET(SSE) / DELETE
├── api/
│   ├── admin.py       /admin/* 管理接口
│   └── app.py         FastAPI 装配
└── sdk/client.py      Agent 侧客户端（含 SSE 监听与心跳循环）
tests/                 26 个测试
examples/              多 Agent 演示 + 常驻 Agent 模板
```

## 快速开始

```bash
# 1. 创建虚拟环境并安装
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate      # Linux / macOS
pip install -e ".[dev]"

# 2. 启动集线器（默认 127.0.0.1:8765）
python -m mcp_hub
# 或指定参数
python -m mcp_hub --host 0.0.0.0 --port 8765 --log-level INFO

# 3. 另开一个终端，跑多 Agent 演示
python examples/multi_agent_demo.py
```

访问 <http://127.0.0.1:8765/docs> 可以看到管理接口的交互式文档。

## MCP 协议

### 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/mcp` | JSON-RPC 请求（`initialize` / `tools/list` / `tools/call` / `ping`） |
| GET | `/mcp` | SSE，接收服务端推送（`?agent_id=xxx` 可直接绑定 Agent 观察） |
| DELETE | `/mcp` | 结束会话 |

会话 id 通过响应头 `Mcp-Session-Id` 下发，客户端后续请求带回该头即可。协议版本协商支持 `2025-06-18` / `2025-03-26` / `2024-11-05`，未知版本回落到最新。

### 工具

| 工具 | 说明 | 关键参数 |
|---|---|---|
| `hub_register` | 注册 / 更新 Agent，并绑定当前会话 | `agent_id`、`name`、`capabilities[]`、`topics[]`、`metadata{}` |
| `hub_heartbeat` | 刷新心跳 | `agent_id`（缺省取会话绑定）、`status`（online/busy） |
| `hub_unregister` | 注销并丢弃待投递消息 | `agent_id` |
| `hub_send` | 发送消息 | `target`（agent_id / `*` / `topic:xxx`）、`payload`、`reply_to` |
| `hub_list_agents` | 查看 Agent 列表 | `online_only` |
| `hub_fetch_messages` | 拉取待投递消息（会移除） | `limit` |
| `hub_subscribe` / `hub_unsubscribe` | 主题订阅管理 | `topic` |

### 示例：手工调一次 MCP

```bash
# 1) 握手，记下响应头里的 Mcp-Session-Id
curl -s -D- -X POST http://127.0.0.1:8765/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'

# 2) 注册（带上第一条拿到的 session id）
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H "Content-Type: application/json" -H "Mcp-Session-Id: <sid>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"hub_register","arguments":{"agent_id":"agent-a"}}}'

# 3) 查看在线 Agent
curl -s http://127.0.0.1:8765/admin/agents
```

### 推送格式

服务端通过 SSE 下发 MCP 标准 logging 通知，消息信封在 `params.data` 里：

```
event: message
data: {"jsonrpc":"2.0","method":"notifications/message","params":{
  "level":"info","logger":"mcp-hub","data":{
    "id":"...","ts":1795...,"sender":"agent-a","target":"agent-b",
    "message_type":"direct","payload":{"task":"ocr"}}}}
```

## 投递语义

- 消息先写入目标 Agent 的**有界队列**（默认 200 条），队列满时丢弃最旧的一条并记 `dropped`。
- 如果目标 Agent 当前有 SSE 连接，同时推入推送队列，实时送达；**推送不会移除队列里的副本**，所以「SSE 收到」和「`hub_fetch_messages` 拉到」只能二选一，不能混用同一条消息——推荐二选一：实时用 SSE，离线用拉取。
- 广播默认不含发送者（`MCP_HUB_BROADCAST_INCLUDE_SENDER=1` 可改）。
- 目标不存在或已离线时进入 `dropped`，发送方能在返回的 `DeliveryReport` 里看到。

## 心跳与离线

```
Agent 每 N 秒 hub_heartbeat → registry.last_seen = now
reaper 每 min(5, timeout/4) 秒扫描 → now - last_seen > heartbeat_timeout → 置 offline + 发事件
offline 超过 offline_retention（默认 600s）→ 从注册表删除
```

判定结果会体现在 `/admin/agents` 的 `online` 字段与 `/admin/metrics` 的 `mcp_hub_agents_online`。

## 管理接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/health` | 健康检查 |
| GET | `/admin/agents?online_only=true&status=online` | Agent 列表（含待投递数） |
| GET | `/admin/agents/{id}` | 单个 Agent 详情 |
| GET | `/admin/agents/{id}/messages?limit=20` | 查看待投递消息（不移除） |
| DELETE | `/admin/agents/{id}` | 强制下线并删除 |
| GET | `/admin/stats` | 汇总统计 |
| GET | `/admin/metrics` | Prometheus 文本指标 |
| GET | `/admin/config` | 脱敏运行配置 |

配置了 `MCP_HUB_ADMIN_TOKEN` 后，以上接口都需要请求头 `X-Admin-Token`。

## 接入监测平台

1. **指标抓取**：`GET /admin/metrics` 直接给 Prometheus / VictoriaMetrics 抓，已包含在线数、消息吞吐、队列积压、SSE 连接数。
2. **事件推送**：配置 `MCP_HUB_WEBHOOK_URLS=http://platform/api/hub-events`，会收到：

   | 事件 | 时机 | data 关键字段 |
   |---|---|---|
   | `agent_registered` | 首次注册 | `agent_id`、`capabilities`、`topics` |
   | `agent_updated` | 重复注册 | 同上 |
   | `agent_offline` | 心跳超时 | `agent_id`、`reason`、`pending_messages` |
   | `agent_unregistered` | 主动注销 / 管理端踢下线 | `agent_id`、`reason`、`pending_dropped` |
   | `message_sent` | 每次发送 | `message_id`、`delivered`、`dropped` |

3. **业务约定**：建议把项目编号、测点类型、报告类型放进 Agent 的 `metadata`，把业务事件名作为 `payload.type`（如 `report.request` / `alarm.raised`），平台侧就能按主题订阅并落库。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `MCP_HUB_HOST` / `MCP_HUB_PORT` | `127.0.0.1` / `8765` | 监听地址 |
| `MCP_HUB_HEARTBEAT_INTERVAL` | `10` | 建议心跳间隔（下发给 Agent 参考） |
| `MCP_HUB_HEARTBEAT_TIMEOUT` | `30` | 心跳超时（秒） |
| `MCP_HUB_OFFLINE_RETENTION` | `600` | 离线保留时长（秒），0 表示不清理 |
| `MCP_HUB_QUEUE_SIZE` | `200` | 每 Agent 待投递队列上限 |
| `MCP_HUB_ADMIN_TOKEN` | 空 | 管理接口令牌 |
| `MCP_HUB_BROADCAST_INCLUDE_SENDER` | `0` | 广播是否含发送者 |
| `MCP_HUB_WEBHOOK_URLS` | 空 | Webhook 地址，逗号分隔 |
| `MCP_HUB_LOG_LEVEL` / `MCP_HUB_LOG_JSON` | `INFO` / `0` | 日志级别 / JSON 日志 |

## 生产部署建议

- **单进程运行**：状态（注册表、队列）在内存里，不要开多 worker；需要横向扩展时，把注册表与总线换成 Redis / NATS 实现即可（接口已经收在 `registry.py` / `bus.py`）。
- 对外暴露时：`--host 0.0.0.0` + 设置 `MCP_HUB_ADMIN_TOKEN`，前面挂 Nginx/Caddy 做 TLS 与限流；SSE 需要关闭代理缓冲（`proxy_buffering off`）。
- 容器化：`python -m mcp_hub` 作为入口，健康检查打 `/admin/health`。

## 已知限制

- 会话与 Agent 的绑定关系在内存中，重启后 Agent 需要重新 `initialize` + `hub_register`。
- SSE 与拉取共用同一个待投递队列（见「投递语义」），同一消息不会被两路各消费一次。
- 没有实现 MCP 的 resources / prompts / sampling（返回空列表），只做工具与推送。
- 未做鉴权（除管理接口），内网部署更合适；需要时可在 `/mcp` 前加一层令牌校验。

## 开发与测试

```bash
pip install -e ".[dev]"
pytest -q            # 26 passed
```

代码风格：类型标注 + 中文注释，模块职责单一；新增工具只需在 `mcp/tools.py` 里加 schema 与 handler。

## 许可证

MIT
