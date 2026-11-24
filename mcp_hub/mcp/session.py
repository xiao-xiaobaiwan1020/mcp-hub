"""MCP 会话管理。

一次 MCP 连接对应一个会话（``Mcp-Session-Id``）。会话与 Agent 的绑定关系在
``hub_register`` 成功时建立，之后的 ``hub_send`` / ``hub_fetch_messages`` 等工具即可
自动以该 Agent 的身份工作。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from ..logging_setup import get_logger
from ..models import Envelope

log = get_logger("mcp_hub.mcp.session")


@dataclass(slots=True)
class Session:
    """一个 MCP 会话。"""

    session_id: str
    created_at: float = field(default_factory=time.time)
    agent_id: str | None = None
    #: 服务端推送队列；SSE 连接存在时由总线写入
    push_queue: asyncio.Queue[Envelope] = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    #: 是否已建立 SSE 长连接
    sse_connected: bool = False
    last_active: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_active = time.time()


class SessionManager:
    """会话集合（内存态）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        """新建会话并返回。"""
        session = Session(session_id=uuid.uuid4().hex)
        self._sessions[session.session_id] = session
        log.debug("session created: %s", session.session_id)
        return session

    def get(self, session_id: str | None) -> Session | None:
        """按 id 取会话。"""
        if not session_id:
            return None
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str | None) -> Session:
        """取会话，不存在则新建（便于无状态客户端调用）。"""
        return self.get(session_id) or self.create()

    def bind_agent(self, session_id: str, agent_id: str) -> Session | None:
        """绑定会话与 Agent。"""
        session = self.get(session_id)
        if session is None:
            return None
        session.agent_id = agent_id
        session.touch()
        return session

    def remove(self, session_id: str | None) -> bool:
        """删除会话。"""
        if not session_id:
            return False
        removed = self._sessions.pop(session_id, None) is not None
        if removed:
            log.debug("session removed: %s", session_id)
        return removed

    def count(self) -> int:
        """活跃会话数。"""
        return len(self._sessions)
