"""运行指标。

提供集线器级别的计数器，并输出 Prometheus 文本格式，方便接入监测平台
（Grafana / VictoriaMetrics / Prometheus 均可直接抓取 ``/admin/metrics``）。
"""

from __future__ import annotations

import time


class Metrics:
    """极简计数器集合（单进程内存态，重启归零）。"""

    def __init__(self) -> None:
        self.started_at = time.time()
        self.messages_sent = 0        # 进入路由的发送请求数
        self.messages_delivered = 0   # 成功写入队列的投递数
        self.messages_dropped = 0     # 目标不存在 / 离线导致的丢弃数
        self.registrations = 0        # 注册次数
        self.heartbeats = 0           # 心跳次数
        self.errors = 0               # 内部错误数
        self.sse_connections = 0      # 当前 SSE 连接数

    @property
    def uptime_seconds(self) -> float:
        return round(time.time() - self.started_at, 3)

    def incr(self, name: str, amount: int = 1) -> None:
        """按属性名累加。"""
        setattr(self, name, getattr(self, name) + amount)

    def render_prometheus(self, agents_total: int, agents_online: int, sessions: int,
                          queue_pending: int) -> str:
        """输出 Prometheus 文本格式指标。"""
        lines = [
            "# HELP mcp_hub_uptime_seconds 集线器运行时长（秒）",
            "# TYPE mcp_hub_uptime_seconds gauge",
            f"mcp_hub_uptime_seconds {self.uptime_seconds}",
            "# HELP mcp_hub_agents_total 注册过的 Agent 总数",
            "# TYPE mcp_hub_agents_total gauge",
            f"mcp_hub_agents_total {agents_total}",
            "# HELP mcp_hub_agents_online 当前在线 Agent 数",
            "# TYPE mcp_hub_agents_online gauge",
            f"mcp_hub_agents_online {agents_online}",
            "# HELP mcp_hub_sessions_active 活跃 MCP 会话数",
            "# TYPE mcp_hub_sessions_active gauge",
            f"mcp_hub_sessions_active {sessions}",
            "# HELP mcp_hub_queue_pending 待投递消息总数",
            "# TYPE mcp_hub_queue_pending gauge",
            f"mcp_hub_queue_pending {queue_pending}",
            "# HELP mcp_hub_messages_sent_total 发送请求总数",
            "# TYPE mcp_hub_messages_sent_total counter",
            f"mcp_hub_messages_sent_total {self.messages_sent}",
            "# HELP mcp_hub_messages_delivered_total 成功投递总数",
            "# TYPE mcp_hub_messages_delivered_total counter",
            f"mcp_hub_messages_delivered_total {self.messages_delivered}",
            "# HELP mcp_hub_messages_dropped_total 丢弃总数",
            "# TYPE mcp_hub_messages_dropped_total counter",
            f"mcp_hub_messages_dropped_total {self.messages_dropped}",
            "# HELP mcp_hub_registrations_total 注册总数",
            "# TYPE mcp_hub_registrations_total counter",
            f"mcp_hub_registrations_total {self.registrations}",
            "# HELP mcp_hub_heartbeats_total 心跳总数",
            "# TYPE mcp_hub_heartbeats_total counter",
            f"mcp_hub_heartbeats_total {self.heartbeats}",
            "# HELP mcp_hub_sse_connections 当前 SSE 连接数",
            "# TYPE mcp_hub_sse_connections gauge",
            f"mcp_hub_sse_connections {self.sse_connections}",
        ]
        return "\n".join(lines) + "\n"
