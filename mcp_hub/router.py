"""消息路由。

支持三种投递形态：

- ``direct``：``target`` 为具体 ``agent_id``，点对点投递；
- ``topic``：``target`` 为 ``topic:<name>``（或显式传 ``topic``），投给所有订阅者；
- ``broadcast``：``target`` 为 ``*`` / ``all``，投给所有在线 Agent（默认不含发送者）。

路由只负责「解析目标 + 写总线 + 记统计」，不关心 Agent 用什么传输（SSE 或拉取）。
"""

from __future__ import annotations

from typing import Any

from .bus import MessageBus
from .config import HubConfig
from .logging_setup import get_logger
from .metrics import Metrics
from .models import AgentStatus, DeliveryReport, Envelope, MessageType
from .registry import AgentRegistry

log = get_logger("mcp_hub.router")

BROADCAST_TARGETS = {"*", "all", "broadcast"}


class Router:
    """消息路由器。"""

    def __init__(self, registry: AgentRegistry, bus: MessageBus,
                 config: HubConfig, metrics: Metrics) -> None:
        self._registry = registry
        self._bus = bus
        self._config = config
        self._metrics = metrics

    async def send(self, sender: str, target: str, payload: dict[str, Any],
                   message_type: MessageType | None = None, topic: str | None = None,
                   reply_to: str | None = None) -> DeliveryReport:
        """发送一条消息并返回投递报告。"""
        message_type = message_type or self._infer_type(target, topic)
        recipients, dropped = self._resolve(sender, target, message_type, topic)

        envelope = Envelope(
            sender=sender,
            target=target,
            message_type=message_type,
            topic=topic if message_type == MessageType.TOPIC else None,
            payload=payload,
            reply_to=reply_to,
        )
        delivered: list[str] = []
        for agent_id in recipients:
            if await self._bus.enqueue(agent_id, envelope):
                delivered.append(agent_id)
                self._registry.add_stats(agent_id, received=1)
            else:
                dropped.append(agent_id)

        self._registry.add_stats(sender, sent=1)
        self._metrics.messages_sent += 1
        self._metrics.messages_delivered += len(delivered)
        self._metrics.messages_dropped += len(dropped)

        log.info("route %s -> %s (%s): delivered=%s dropped=%s",
                 sender, target, message_type.value, delivered, dropped)
        return DeliveryReport(
            message_id=envelope.id,
            delivered=delivered,
            dropped=dropped,
            queued=len(delivered),
        )

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _infer_type(target: str, topic: str | None) -> MessageType:
        if topic is not None or target.startswith("topic:"):
            return MessageType.TOPIC
        if target in BROADCAST_TARGETS:
            return MessageType.BROADCAST
        return MessageType.DIRECT

    def _resolve(self, sender: str, target: str, message_type: MessageType,
                 topic: str | None) -> tuple[list[str], list[str]]:
        """解析投递目标，返回 (接收者列表, 丢弃列表)。"""
        if message_type == MessageType.BROADCAST:
            recipients = self._registry.online_ids()
            if not self._config.broadcast_include_sender:
                recipients = [agent_id for agent_id in recipients if agent_id != sender]
            return recipients, []

        if message_type == MessageType.TOPIC:
            topic_name = topic or target.removeprefix("topic:")
            if not topic_name:
                return [], []
            return self._registry.topic_subscribers(topic_name), []

        # direct
        view = self._registry.get(target)
        if view is None or view.status == AgentStatus.OFFLINE:
            return [], [target]
        return [target], []
