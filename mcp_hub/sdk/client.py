"""Agent 侧客户端（MCP Streamable HTTP）。

用法::

    async with HubClient("http://127.0.0.1:8765", agent_id="agent-a") as hub:
        await hub.initialize()
        await hub.register(name="A", capabilities=["report"])
        async for message in hub.events():      # SSE 实时接收
            print(message)
        # 或轮询：await hub.fetch_messages()

为了方便演示，客户端把每个 MCP 工具都封装成了一个方法，并把 ``tools/call`` 返回的
JSON 文本解析回 Python 对象。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

SESSION_HEADER = "Mcp-Session-Id"


class HubError(RuntimeError):
    """集线器返回的 JSON-RPC 错误或工具错误。"""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class HubClient:
    """集线器客户端。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8765",
                 agent_id: str | None = None, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.session_id: str | None = None
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout,
                                         transport=transport)
        self._rpc_id = 0

    # ------------------------------------------------------------------ 生命周期
    async def __aenter__(self) -> "HubClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """关闭底层 HTTP 连接。"""
        await self._client.aclose()

    # ------------------------------------------------------------------ 协议
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return headers

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """发送一条 JSON-RPC 请求并返回 result。"""
        self._rpc_id += 1
        payload = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method,
                   "params": params or {}}
        response = await self._client.post("/mcp", json=payload, headers=self._headers())
        session_id = response.headers.get(SESSION_HEADER)
        if session_id:
            self.session_id = session_id
        response.raise_for_status()
        # 通知类请求（notifications/*）服务端返回 202 且无响应体
        if response.status_code == 202 or not response.content:
            return {}
        body = response.json()
        if "error" in body:
            raise HubError(str(body["error"].get("message")), body["error"].get("code"))
        return body.get("result") or {}

    async def initialize(self) -> dict[str, Any]:
        """MCP 握手。"""
        result = await self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": self.agent_id or "mcp-hub-client", "version": "0.1.0"},
        })
        await self._rpc("notifications/initialized")
        return result

    async def _tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用一个集线器工具并把 JSON 文本解析回对象。"""
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content") or []
        text = content[0].get("text", "{}") if content else "{}"
        if result.get("isError"):
            raise HubError(text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    # ------------------------------------------------------------------ 业务方法
    async def register(self, name: str | None = None, capabilities: list[str] | None = None,
                       topics: list[str] | None = None, metadata: dict[str, Any] | None = None,
                       version: str | None = "0.1.0", agent_id: str | None = None) -> dict[str, Any]:
        """注册 Agent（并绑定当前会话）。"""
        agent_id = agent_id or self.agent_id
        if not agent_id:
            raise HubError("agent_id 未设置")
        self.agent_id = agent_id
        return await self._tool("hub_register", {
            "agent_id": agent_id,
            "name": name or agent_id,
            "version": version,
            "capabilities": capabilities or [],
            "topics": topics or [],
            "metadata": metadata or {},
        })

    async def heartbeat(self, status: str | None = None) -> dict[str, Any]:
        """发送心跳。"""
        args: dict[str, Any] = {}
        if self.agent_id:
            args["agent_id"] = self.agent_id
        if status:
            args["status"] = status
        return await self._tool("hub_heartbeat", args)

    async def unregister(self) -> dict[str, Any]:
        """注销 Agent。"""
        return await self._tool("hub_unregister", {"agent_id": self.agent_id})

    async def send(self, target: str, payload: dict[str, Any], *,
                   message_type: str | None = None, topic: str | None = None,
                   reply_to: str | None = None) -> dict[str, Any]:
        """发送消息。"""
        args: dict[str, Any] = {"target": target, "payload": payload}
        if message_type:
            args["message_type"] = message_type
        if topic:
            args["topic"] = topic
        if reply_to:
            args["reply_to"] = reply_to
        return await self._tool("hub_send", args)

    async def list_agents(self, online_only: bool = False) -> dict[str, Any]:
        """列出 Agent。"""
        return await self._tool("hub_list_agents", {"online_only": online_only})

    async def fetch_messages(self, limit: int = 50) -> dict[str, Any]:
        """拉取待投递消息（会移除）。"""
        return await self._tool("hub_fetch_messages", {"limit": limit})

    async def subscribe(self, topic: str) -> dict[str, Any]:
        """订阅主题。"""
        return await self._tool("hub_subscribe", {"topic": topic})

    async def unsubscribe(self, topic: str) -> dict[str, Any]:
        """取消订阅。"""
        return await self._tool("hub_unsubscribe", {"topic": topic})

    # ------------------------------------------------------------------ 实时接收
    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """建立 SSE 长连接，逐条产出消息信封。"""
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        params = {"agent_id": self.agent_id} if self.agent_id else {}
        async with self._client.stream("GET", "/mcp", headers=headers, params=params) as response:
            response.raise_for_status()
            session_id = response.headers.get(SESSION_HEADER)
            if session_id:
                self.session_id = session_id
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                data = (payload.get("params") or {}).get("data")
                if isinstance(data, dict):
                    yield data

    async def heartbeat_loop(self, interval: float = 10.0,
                             stop: asyncio.Event | None = None) -> None:
        """后台心跳循环（``stop`` 置位后退出）。"""
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.heartbeat()
            except Exception:  # noqa: BLE001 - 心跳失败不应终止 Agent
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
