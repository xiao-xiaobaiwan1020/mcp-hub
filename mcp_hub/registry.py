"""Agent 注册表。

集中管理 Agent 的身份、能力、主题订阅与心跳时间，并负责：
- 注册 / 注销 / 心跳（``register`` / ``unregister`` / ``heartbeat``）
- 在线判定：``now - last_seen <= heartbeat_timeout``
- 心跳超时扫描（``mark_stale``）与长期离线清理（``prune``）
- 主题订阅解析（``topic_subscribers``）
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .models import AgentRegistration, AgentStats, AgentStatus, AgentView
from .logging_setup import get_logger

log = get_logger("mcp_hub.registry")


@dataclass(slots=True)
class AgentRecord:
    """注册表中的内部记录（可变，仅在持有注册表锁时修改）。"""

    agent_id: str
    name: str | None
    version: str | None
    capabilities: list[str]
    topics: list[str]
    metadata: dict
    status: AgentStatus
    registered_at: float
    last_seen: float
    stats: AgentStats = field(default_factory=AgentStats)

    def to_view(self, now: float, timeout: float) -> AgentView:
        """转换为对外视图，并计算在线状态。"""
        online = self.status in (AgentStatus.ONLINE, AgentStatus.BUSY) and (now - self.last_seen) <= timeout
        return AgentView(
            agent_id=self.agent_id,
            name=self.name,
            version=self.version,
            capabilities=list(self.capabilities),
            topics=list(self.topics),
            metadata=dict(self.metadata),
            status=self.status if online else AgentStatus.OFFLINE,
            online=online,
            registered_at=self.registered_at,
            last_seen=self.last_seen,
            last_seen_ago=round(max(0.0, now - self.last_seen), 3),
            stats=self.stats.model_copy(deep=True),
        )


class AgentRegistry:
    """线程安全（asyncio 单线程模型 + 锁）的 Agent 注册表。"""

    def __init__(self, heartbeat_timeout: float = 30.0, offline_retention: float = 600.0) -> None:
        self._agents: dict[str, AgentRecord] = {}
        self._lock = asyncio.Lock()
        self._timeout = heartbeat_timeout
        self._retention = offline_retention

    # ------------------------------------------------------------------ 查询
    def get(self, agent_id: str) -> AgentView | None:
        """按 agent_id 取视图；不存在返回 None。"""
        record = self._agents.get(agent_id)
        return None if record is None else record.to_view(time.time(), self._timeout)

    def is_online(self, agent_id: str) -> bool:
        """Agent 是否在线。"""
        view = self.get(agent_id)
        return bool(view and view.online)

    def list(self, status: AgentStatus | None = None, online_only: bool = False) -> list[AgentView]:
        """列出所有 Agent（可按状态 / 在线过滤），按 agent_id 排序。"""
        now = time.time()
        views = [record.to_view(now, self._timeout) for record in self._agents.values()]
        if status is not None:
            views = [view for view in views if view.status == status]
        if online_only:
            views = [view for view in views if view.online]
        return sorted(views, key=lambda item: item.agent_id)

    def online_ids(self) -> list[str]:
        """所有在线 Agent 的 id。"""
        now = time.time()
        return sorted(
            record.agent_id
            for record in self._agents.values()
            if record.status in (AgentStatus.ONLINE, AgentStatus.BUSY)
            and (now - record.last_seen) <= self._timeout
        )

    def topic_subscribers(self, topic: str) -> list[str]:
        """订阅了指定主题且当前在线的 Agent。"""
        now = time.time()
        result = []
        for record in self._agents.values():
            if topic not in record.topics:
                continue
            if record.status not in (AgentStatus.ONLINE, AgentStatus.BUSY):
                continue
            if (now - record.last_seen) > self._timeout:
                continue
            result.append(record.agent_id)
        return sorted(result)

    def counts(self) -> tuple[int, int]:
        """返回 (总数, 在线数)。"""
        now = time.time()
        total = len(self._agents)
        online = sum(
            1
            for record in self._agents.values()
            if record.status in (AgentStatus.ONLINE, AgentStatus.BUSY)
            and (now - record.last_seen) <= self._timeout
        )
        return total, online

    # ------------------------------------------------------------------ 写操作
    async def register(self, registration: AgentRegistration) -> tuple[AgentView, bool]:
        """注册或更新一个 Agent。

        返回 ``(视图, 是否新建)``。重复注册视为更新（保留累计统计与注册时间）。
        """
        now = time.time()
        async with self._lock:
            record = self._agents.get(registration.agent_id)
            created = record is None
            if record is None:
                record = AgentRecord(
                    agent_id=registration.agent_id,
                    name=registration.name,
                    version=registration.version,
                    capabilities=list(registration.capabilities),
                    topics=list(registration.topics),
                    metadata=dict(registration.metadata),
                    status=registration.status,
                    registered_at=now,
                    last_seen=now,
                )
                self._agents[record.agent_id] = record
            else:
                # 更新可变字段；registered_at / stats 保持不变
                record.name = registration.name
                record.version = registration.version
                record.capabilities = list(registration.capabilities)
                record.topics = list(registration.topics)
                record.metadata = dict(registration.metadata)
                record.status = registration.status
                record.last_seen = now
            view = record.to_view(now, self._timeout)
        log.info("agent %s %s", "registered" if created else "re-registered", registration.agent_id)
        return view, created

    async def heartbeat(self, agent_id: str, status: AgentStatus | None = None) -> AgentView | None:
        """刷新心跳；Agent 不存在返回 None。"""
        now = time.time()
        async with self._lock:
            record = self._agents.get(agent_id)
            if record is None:
                return None
            record.last_seen = now
            if status is not None:
                record.status = status
            elif record.status == AgentStatus.OFFLINE:
                record.status = AgentStatus.ONLINE
            view = record.to_view(now, self._timeout)
        return view

    async def unregister(self, agent_id: str) -> bool:
        """注销 Agent（返回是否真的删除）。"""
        async with self._lock:
            return self._agents.pop(agent_id, None) is not None

    async def subscribe(self, agent_id: str, topic: str) -> bool:
        """订阅主题；Agent 不存在返回 False。"""
        async with self._lock:
            record = self._agents.get(agent_id)
            if record is None:
                return False
            if topic not in record.topics:
                record.topics.append(topic)
                record.last_seen = time.time()
            return True

    async def unsubscribe(self, agent_id: str, topic: str) -> bool:
        """取消订阅；返回该 Agent 是否存在。"""
        async with self._lock:
            record = self._agents.get(agent_id)
            if record is None:
                return False
            if topic in record.topics:
                record.topics.remove(topic)
            return True

    def add_stats(self, agent_id: str, *, sent: int = 0, received: int = 0,
                  dropped: int = 0, errors: int = 0) -> None:
        """累加某个 Agent 的统计（供总线 / 路由调用）。"""
        record = self._agents.get(agent_id)
        if record is None:
            return
        record.stats.sent += sent
        record.stats.received += received
        record.stats.dropped += dropped
        record.stats.errors += errors

    async def mark_stale(self) -> list[str]:
        """把心跳超时的 Agent 置为离线，返回本次状态变化的 agent_id 列表。"""
        now = time.time()
        changed: list[str] = []
        async with self._lock:
            for record in self._agents.values():
                if record.status in (AgentStatus.ONLINE, AgentStatus.BUSY) and (now - record.last_seen) > self._timeout:
                    record.status = AgentStatus.OFFLINE
                    changed.append(record.agent_id)
        for agent_id in changed:
            log.warning("agent offline (heartbeat timeout): %s", agent_id)
        return changed

    async def prune(self) -> list[str]:
        """清理离线超过保留期的 Agent，返回被清理的 agent_id 列表。"""
        if self._retention <= 0:
            return []
        now = time.time()
        removed: list[str] = []
        async with self._lock:
            for agent_id, record in list(self._agents.items()):
                if record.status == AgentStatus.OFFLINE and (now - record.last_seen) > self._retention:
                    del self._agents[agent_id]
                    removed.append(agent_id)
        if removed:
            log.info("pruned offline agents: %s", ", ".join(removed))
        return removed
