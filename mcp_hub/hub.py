"""服务容器与生命周期。

``Hub`` 把注册表、总线、路由、指标、会话、Webhook 组装在一起，并负责后台的心跳
超时扫描（reaper）。FastAPI 应用只依赖这一个对象，测试时也可以直接实例化。
"""

from __future__ import annotations

import asyncio

from .bus import MessageBus
from .config import HubConfig
from .events import EventDispatcher
from .logging_setup import get_logger
from .metrics import Metrics
from .models import HubHealth, HubStats
from .registry import AgentRegistry
from .router import Router
from .mcp.session import SessionManager
from .mcp.tools import HubTools
from . import __version__

log = get_logger("mcp_hub.hub")


class Hub:
    """集线器服务容器。"""

    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self.registry = AgentRegistry(
            heartbeat_timeout=config.heartbeat_timeout,
            offline_retention=config.offline_retention,
        )
        self.bus = MessageBus(queue_size=config.queue_size)
        self.metrics = Metrics()
        self.router = Router(self.registry, self.bus, config, self.metrics)
        self.sessions = SessionManager()
        self.events = EventDispatcher(config.webhook_urls, timeout=config.webhook_timeout)
        self.tools = HubTools(self.registry, self.router, self.bus, config, self.metrics, self.events)
        self._reaper_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        """启动后台任务。"""
        await self.events.start()
        self._reaper_task = asyncio.create_task(self._reaper_loop(), name="hub-reaper")
        log.info("hub started (heartbeat_timeout=%.1fs, retention=%.1fs, webhooks=%d)",
                 self.config.heartbeat_timeout, self.config.offline_retention,
                 len(self.config.webhook_urls))

    async def stop(self) -> None:
        """停止后台任务并释放资源。"""
        self._stopping.set()
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            await asyncio.gather(self._reaper_task, return_exceptions=True)
            self._reaper_task = None
        await self.events.close()
        log.info("hub stopped")

    async def _reaper_loop(self) -> None:
        """周期扫描：心跳超时置离线、清理过期 Agent。"""
        interval = max(1.0, min(5.0, self.config.heartbeat_timeout / 4))
        while not self._stopping.is_set():
            try:
                changed = await self.registry.mark_stale()
                for agent_id in changed:
                    self.events.emit("agent_offline", {
                        "agent_id": agent_id,
                        "reason": "heartbeat_timeout",
                        "pending_messages": self.bus.pending_size(agent_id),
                    })
                await self.registry.prune()
            except asyncio.CancelledError:  # pragma: no cover - 正常退出
                raise
            except Exception:  # noqa: BLE001 - reaper 不应中断
                log.exception("reaper iteration failed")
            await asyncio.sleep(interval)

    # ------------------------------------------------------------------ 视图
    def health(self) -> HubHealth:
        """健康检查视图。"""
        total, online = self.registry.counts()
        return HubHealth(
            version=__version__,
            uptime_seconds=self.metrics.uptime_seconds,
            agents_online=online,
            agents_total=total,
        )

    def stats(self) -> HubStats:
        """整体统计视图。"""
        total, online = self.registry.counts()
        return HubStats(
            uptime_seconds=self.metrics.uptime_seconds,
            agents_total=total,
            agents_online=online,
            messages_sent=self.metrics.messages_sent,
            messages_delivered=self.metrics.messages_delivered,
            messages_dropped=self.metrics.messages_dropped,
            sessions_active=self.sessions.count(),
            queue_pending=self.bus.total_pending(),
        )
