"""注册表 / 心跳 / 主题订阅的单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from mcp_hub.models import AgentRegistration, AgentStatus
from mcp_hub.registry import AgentRegistry


@pytest.fixture
def registry() -> AgentRegistry:
    return AgentRegistry(heartbeat_timeout=0.3, offline_retention=60.0)


async def test_register_and_list(registry: AgentRegistry) -> None:
    view, created = await registry.register(AgentRegistration(
        agent_id="agent-a", name="A", capabilities=["report"], topics=["monitor/alarm"],
    ))
    assert created is True
    assert view.online is True
    assert view.status == AgentStatus.ONLINE
    assert [item.agent_id for item in registry.list()] == ["agent-a"]
    assert registry.online_ids() == ["agent-a"]


async def test_reregister_keeps_stats_and_registered_at(registry: AgentRegistry) -> None:
    first, _ = await registry.register(AgentRegistration(agent_id="agent-a"))
    registry.add_stats("agent-a", sent=3)
    second, created = await registry.register(AgentRegistration(agent_id="agent-a", name="A2"))
    assert created is False
    assert second.name == "A2"
    assert second.stats.sent == 3
    assert second.registered_at == pytest.approx(first.registered_at, rel=1e-6)


async def test_heartbeat_timeout_marks_offline(registry: AgentRegistry) -> None:
    await registry.register(AgentRegistration(agent_id="agent-a"))
    assert registry.is_online("agent-a") is True
    await asyncio.sleep(0.4)
    changed = await registry.mark_stale()
    assert changed == ["agent-a"]
    assert registry.is_online("agent-a") is False
    assert registry.get("agent-a").status == AgentStatus.OFFLINE


async def test_heartbeat_revives_offline_agent(registry: AgentRegistry) -> None:
    await registry.register(AgentRegistration(agent_id="agent-a"))
    await asyncio.sleep(0.4)
    await registry.mark_stale()
    view = await registry.heartbeat("agent-a")
    assert view is not None and view.online is True


async def test_topic_subscription_and_online_filter(registry: AgentRegistry) -> None:
    await registry.register(AgentRegistration(agent_id="agent-a", topics=["monitor/alarm"]))
    await registry.register(AgentRegistration(agent_id="agent-b", topics=["monitor/alarm", "report"]))
    await registry.register(AgentRegistration(agent_id="agent-c", topics=["report"]))
    assert registry.topic_subscribers("monitor/alarm") == ["agent-a", "agent-b"]
    assert registry.topic_subscribers("report") == ["agent-b", "agent-c"]
    assert registry.topic_subscribers("nope") == []

    await registry.unsubscribe("agent-a", "monitor/alarm")
    assert registry.topic_subscribers("monitor/alarm") == ["agent-b"]


async def test_unregister(registry: AgentRegistry) -> None:
    await registry.register(AgentRegistration(agent_id="agent-a"))
    assert await registry.unregister("agent-a") is True
    assert await registry.unregister("agent-a") is False
    assert registry.get("agent-a") is None
