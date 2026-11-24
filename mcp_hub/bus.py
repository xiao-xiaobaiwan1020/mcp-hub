"""消息总线。

每个 Agent 拥有一个待投递队列（``deque``，有界）和一个可选的推送出口（``asyncio.Queue``，
对应一条 SSE 连接）。投递策略：

1. 消息先进入待投递队列，保证「Agent 暂时没有 SSE 连接」时也能通过 ``hub_fetch_messages`` 拉取；
2. 若该 Agent 当前有 SSE 连接，则同时推入对应队列，实现实时下发；
3. 队列满时丢弃最旧消息（背压保护），并在统计中记一次 dropped。
"""

from __future__ import annotations

import asyncio
from collections import deque

from .logging_setup import get_logger
from .models import Envelope

log = get_logger("mcp_hub.bus")


class MessageBus:
    """有界、可拉取 + 可推送的消息总线。"""

    def __init__(self, queue_size: int = 200) -> None:
        self._size = max(1, queue_size)
        #: agent_id -> 待投递消息（先进先出）
        self._pending: dict[str, deque[Envelope]] = {}
        #: agent_id -> 该 Agent 的 SSE 推送队列（可能多条连接）
        self._sinks: dict[str, list[asyncio.Queue[Envelope]]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ 状态
    def pending_size(self, agent_id: str) -> int:
        """某个 Agent 的待投递条数。"""
        return len(self._pending.get(agent_id, ()))

    def total_pending(self) -> int:
        """全局待投递条数。"""
        return sum(len(queue) for queue in self._pending.values())

    # ------------------------------------------------------------------ 投递
    async def enqueue(self, agent_id: str, envelope: Envelope) -> bool:
        """把消息放入 Agent 的待投递队列，并尝试实时推送。返回是否入队成功。"""
        async with self._lock:
            queue = self._pending.get(agent_id)
            if queue is None:
                queue = deque(maxlen=self._size)
                self._pending[agent_id] = queue
            if len(queue) >= self._size:
                queue.popleft()
                log.warning("queue full for %s, dropped oldest message", agent_id)
            queue.append(envelope)
            sinks = list(self._sinks.get(agent_id, ()))
        # 推送放在锁外，避免慢消费者阻塞其它 Agent
        for sink in sinks:
            try:
                sink.put_nowait(envelope)
            except asyncio.QueueFull:
                # 推送队列满：丢弃最旧的一条，保持实时性
                try:
                    sink.get_nowait()
                    sink.put_nowait(envelope)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    log.warning("push queue full for %s, message kept pending only", agent_id)
        return True

    async def drain(self, agent_id: str, limit: int = 50) -> list[Envelope]:
        """拉取并移除至多 ``limit`` 条待投递消息（供 ``hub_fetch_messages`` 使用）。"""
        async with self._lock:
            queue = self._pending.get(agent_id)
            if not queue:
                return []
            take = min(limit, len(queue))
            return [queue.popleft() for _ in range(take)]

    async def peek(self, agent_id: str, limit: int = 50) -> list[Envelope]:
        """查看但不移除待投递消息（供管理接口观察队列）。"""
        async with self._lock:
            queue = list(self._pending.get(agent_id, ()))
        return queue[:limit]

    # ------------------------------------------------------------------ SSE sink
    async def attach_sink(self, agent_id: str, queue: asyncio.Queue[Envelope]) -> None:
        """为 Agent 挂一条 SSE 推送出口，并把已有待投递消息补发过去。"""
        async with self._lock:
            self._sinks.setdefault(agent_id, []).append(queue)
            backlog = list(self._pending.get(agent_id, ()))
        for envelope in backlog:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                break
        log.info("sse sink attached for %s (backlog=%d)", agent_id, len(backlog))

    async def detach_sink(self, agent_id: str, queue: asyncio.Queue[Envelope]) -> None:
        """移除 SSE 推送出口。"""
        async with self._lock:
            sinks = self._sinks.get(agent_id)
            if not sinks:
                return
            if queue in sinks:
                sinks.remove(queue)
            if not sinks:
                self._sinks.pop(agent_id, None)
        log.info("sse sink detached for %s", agent_id)

    async def drop_agent(self, agent_id: str) -> int:
        """丢弃某 Agent 的全部待投递消息并断开推送出口（用于注销）。"""
        async with self._lock:
            queue = self._pending.pop(agent_id, None)
            self._sinks.pop(agent_id, None)
        return 0 if queue is None else len(queue)
