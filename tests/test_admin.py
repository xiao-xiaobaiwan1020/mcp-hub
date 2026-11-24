"""管理接口测试。"""

from __future__ import annotations

import httpx

from mcp_hub.api.app import create_app
from mcp_hub.config import HubConfig
from tests.conftest import call_tool, initialize


async def test_root_and_health(client_and_hub) -> None:
    client, _, _ = client_and_hub
    root = (await client.get("/")).json()
    assert root["service"] == "mcp-hub"
    assert root["protocol"]["endpoint"] == "/mcp"

    health = (await client.get("/admin/health")).json()
    assert health["status"] == "ok"
    assert health["agents_total"] == 0


async def test_agents_list_and_detail(client_and_hub) -> None:
    client, _, _ = client_and_hub
    session = await initialize(client)
    await call_tool(client, "hub_register", {"agent_id": "agent-a", "name": "A",
                                              "capabilities": ["report"]}, session)

    listing = (await client.get("/admin/agents")).json()
    assert listing["total"] == 1
    assert listing["online"] == 1
    agent = listing["agents"][0]
    assert agent["agent_id"] == "agent-a"
    assert agent["pending_messages"] == 0
    assert agent["online"] is True

    detail = (await client.get("/admin/agents/agent-a")).json()
    assert detail["name"] == "A"

    missing = await client.get("/admin/agents/ghost")
    assert missing.status_code == 404


async def test_pending_messages_view_and_kick(client_and_hub) -> None:
    client, _, _ = client_and_hub
    session_a = await initialize(client)
    session_b = await initialize(client)
    await call_tool(client, "hub_register", {"agent_id": "a"}, session_a)
    await call_tool(client, "hub_register", {"agent_id": "b"}, session_b)
    await call_tool(client, "hub_send", {"target": "b", "payload": {"n": 1}}, session_a)

    peek = (await client.get("/admin/agents/b/messages")).json()
    assert peek["pending"] == 1
    assert peek["messages"][0]["payload"] == {"n": 1}

    # 查看不移除
    still = (await client.get("/admin/agents/b")).json()
    assert still["pending_messages"] == 1

    kicked = (await client.delete("/admin/agents/b")).json()
    assert kicked["removed"] is True
    assert kicked["pending_dropped"] == 1


async def test_stats_metrics_config(client_and_hub) -> None:
    client, _, _ = client_and_hub
    session = await initialize(client)
    await call_tool(client, "hub_register", {"agent_id": "a"}, session)
    await call_tool(client, "hub_send", {"target": "a", "payload": {}}, session)

    stats = (await client.get("/admin/stats")).json()
    assert stats["messages_sent"] == 1
    assert stats["agents_total"] == 1
    assert stats["sessions_active"] >= 1

    metrics = (await client.get("/admin/metrics")).text
    assert "mcp_hub_agents_online" in metrics
    assert "mcp_hub_messages_sent_total 1" in metrics

    config = (await client.get("/admin/config")).json()
    assert config["admin_token_set"] is False
    assert "admin_token" not in config


async def test_admin_token_required() -> None:
    app = create_app(HubConfig(admin_token="s3cret"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
        denied = await client.get("/admin/health")
        assert denied.status_code == 401
        allowed = await client.get("/admin/health", headers={"X-Admin-Token": "s3cret"})
        assert allowed.status_code == 200
