"""MCP HTTP 协议层测试：握手、工具列表、注册、收发、拉取。"""

from __future__ import annotations

import pytest

from tests.conftest import call_tool, initialize, rpc


async def test_initialize_negotiates_version(client_and_hub) -> None:
    client, hub, _ = client_and_hub
    response = await client.post("/mcp", json=rpc("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1.0"},
    }))
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["protocolVersion"] == "2025-06-18"
    assert body["result"]["serverInfo"]["name"] == "mcp-hub"
    assert response.headers["Mcp-Session-Id"]


async def test_unknown_version_falls_back(client_and_hub) -> None:
    client, _, _ = client_and_hub
    response = await client.post("/mcp", json=rpc("initialize", {"protocolVersion": "1999-01-01"}))
    assert response.json()["result"]["protocolVersion"] == "2025-06-18"


async def test_tools_list_contains_all_tools(client_and_hub) -> None:
    client, _, _ = client_and_hub
    response = await client.post("/mcp", json=rpc("tools/list"))
    tools = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert {"hub_register", "hub_heartbeat", "hub_send", "hub_list_agents",
            "hub_fetch_messages", "hub_subscribe", "hub_unregister"} <= tools


async def test_register_send_and_fetch(client_and_hub) -> None:
    client, hub, _ = client_and_hub
    session_a = await initialize(client)
    session_b = await initialize(client)

    registered = await call_tool(client, "hub_register", {"agent_id": "agent-a", "name": "A",
                                                          "topics": ["alarm"]}, session_a)
    assert registered["created"] is True
    await call_tool(client, "hub_register", {"agent_id": "agent-b", "name": "B"}, session_b)

    report = await call_tool(client, "hub_send", {"target": "agent-b", "payload": {"task": "ocr"}},
                             session_a)
    assert report["delivered"] == ["agent-b"]

    fetched = await call_tool(client, "hub_fetch_messages", {"limit": 10}, session_b)
    assert fetched["count"] == 1
    assert fetched["messages"][0]["payload"] == {"task": "ocr"}

    # 拉取之后再取为空
    again = await call_tool(client, "hub_fetch_messages", {}, session_b)
    assert again["count"] == 0


async def test_broadcast_and_topic(client_and_hub) -> None:
    client, _, _ = client_and_hub
    sessions = {}
    for agent_id in ("a", "b", "c"):
        sessions[agent_id] = await initialize(client)
        await call_tool(client, "hub_register", {"agent_id": agent_id}, sessions[agent_id])

    await call_tool(client, "hub_subscribe", {"topic": "alarm"}, sessions["b"])
    report = await call_tool(client, "hub_send", {"target": "topic:alarm", "payload": {"x": 1}},
                             sessions["a"])
    assert report["delivered"] == ["b"]

    broadcast = await call_tool(client, "hub_send", {"target": "*", "payload": {"y": 2}},
                                sessions["a"])
    assert sorted(broadcast["delivered"]) == ["b", "c"]


async def test_heartbeat_and_offline(client_and_hub) -> None:
    client, hub, _ = client_and_hub
    session = await initialize(client)
    await call_tool(client, "hub_register", {"agent_id": "agent-a"}, session)

    beat = await call_tool(client, "hub_heartbeat", {"status": "busy"}, session)
    assert beat["agent"]["status"] == "busy"
    assert beat["agent"]["online"] is True

    # 手动触发超时扫描：把 last_seen 提前
    record = hub.registry._agents["agent-a"]  # noqa: SLF001 - 测试用
    record.last_seen -= hub.config.heartbeat_timeout + 1
    changed = await hub.registry.mark_stale()
    assert changed == ["agent-a"]

    listed = await call_tool(client, "hub_list_agents", {"online_only": True})
    assert listed["total"] == 0


async def test_unregister_drops_pending(client_and_hub) -> None:
    client, hub, _ = client_and_hub
    session_a = await initialize(client)
    session_b = await initialize(client)
    await call_tool(client, "hub_register", {"agent_id": "a"}, session_a)
    await call_tool(client, "hub_register", {"agent_id": "b"}, session_b)
    await call_tool(client, "hub_send", {"target": "b", "payload": {}}, session_a)
    assert hub.bus.pending_size("b") == 1

    result = await call_tool(client, "hub_unregister", {"agent_id": "b"}, session_b)
    assert result["removed"] is True
    assert result["pending_dropped"] == 1
    assert hub.bus.pending_size("b") == 0


async def test_invalid_tool_returns_error(client_and_hub) -> None:
    client, _, _ = client_and_hub
    response = await client.post("/mcp", json=rpc("tools/call", {"name": "nope", "arguments": {}}))
    body = response.json()
    assert body["error"]["code"] == -32602


async def test_notification_returns_202(client_and_hub) -> None:
    client, _, _ = client_and_hub
    response = await client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 202
