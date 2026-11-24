"""公共测试夹具。

用 ``httpx.ASGITransport`` 直接驱动应用，手工启停 ``Hub``（不依赖 TestClient），
这样既能测 HTTP 协议层，也能直接操作内部状态。
"""

from __future__ import annotations

import httpx
import pytest

from mcp_hub.api.app import create_app
from mcp_hub.config import HubConfig


def rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    """构造一条 JSON-RPC 请求体。"""
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


@pytest.fixture
async def client_and_hub():
    """启动一个集线器实例，返回 (httpx 客户端, Hub, FastAPI app)。"""
    config = HubConfig(
        heartbeat_interval=1.0,
        heartbeat_timeout=5.0,
        offline_retention=60.0,
        queue_size=50,
    )
    app = create_app(config)
    hub = app.state.hub
    await hub.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
        yield client, hub, app
    await hub.stop()


async def call_tool(client: httpx.AsyncClient, name: str, arguments: dict,
                    session_id: str | None = None) -> dict:
    """调用一个 MCP 工具并返回解析后的 dict。"""
    headers = {"Mcp-Session-Id": session_id} if session_id else {}
    response = await client.post("/mcp", json=rpc("tools/call", {"name": name, "arguments": arguments}),
                                 headers=headers)
    body = response.json()
    assert "result" in body, body
    result = body["result"]
    content = result.get("content") or []
    import json
    return json.loads(content[0]["text"]) if content else {}


async def initialize(client: httpx.AsyncClient, session_id: str | None = None) -> str:
    """握手并返回会话 id。"""
    headers = {"Mcp-Session-Id": session_id} if session_id else {}
    response = await client.post("/mcp", json=rpc("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1.0"},
    }), headers=headers)
    assert response.status_code == 200
    return response.headers["Mcp-Session-Id"]
