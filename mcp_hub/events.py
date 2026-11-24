"""Webhook 事件分发。

集线器在上线 / 离线 / 注册 / 发送等关键节点产生事件，按配置 POST 给监测平台。
所有发送均为「即发即忘」：失败只记日志，不影响主流程。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .logging_setup import get_logger

log = get_logger("mcp_hub.events")


class EventDispatcher:
    """Webhook 事件分发器。"""

    def __init__(self, urls: list[str], timeout: float = 5.0) -> None:
        self._urls = list(urls)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._urls)

    async def start(self) -> None:
        """创建共享 HTTP 客户端。"""
        if self.enabled and self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        """取消未完成任务并关闭客户端。"""
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """非阻塞地派发一个事件。"""
        if not self._urls or self._client is None:
            return
        payload = {"event": event, "ts": time.time(), "data": data}
        task = asyncio.create_task(self._broadcast(payload), name=f"webhook:{event}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        assert self._client is not None
        for url in self._urls:
            try:
                response = await self._client.post(url, json=payload)
                if response.status_code >= 400:
                    log.warning("webhook %s -> HTTP %s", url, response.status_code)
            except Exception as exc:  # noqa: BLE001 - webhook 失败不应影响主流程
                log.warning("webhook %s failed: %s", url, exc)
