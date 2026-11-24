"""路由与消息总线测试（不经过 HTTP）。"""

from __future__ import annotations

import pytest

from mcp_hub.bus import MessageBus
from mcp_hub.config import HubConfig
from mcp_hub.metrics import Metrics
from mcp_hub.models import AgentRegistration, MessageType
from mcp_hub.registry import AgentRegistry
from mcp_hub.router import Router


@pytest.fixture
async def router_env():
    config = HubConfig()
    registry = AgentRegistry(heartbeat_timeout=30.0)
    bus = MessageBus(queue_size=10)
    metrics = Metrics()
    router = Router(registry, bus, config, metrics)
    await registry.register(AgentRegistration(agent_id="a", topics=["alarm"]))
    await registry.register(AgentRegistration(agent_id="b", topics=["alarm", "report"]))
    await registry.register(AgentRegistration(agent_id="c", topics=["report"]))
    return router, registry, bus, metrics


async def test_direct_message(router_env) -> None:
    router, _, bus, metrics = router_env
    report = await router.send("a", "b", {"text": "hello"})
    assert report.delivered == ["b"]
    assert bus.pending_size("b") == 1
    assert metrics.messages_sent == 1
    assert metrics.messages_delivered == 1

    messages = await bus.drain("b")
    assert len(messages) == 1
    assert messages[0].sender == "a"
    assert messages[0].payload == {"text": "hello"}
    assert messages[0].message_type == MessageType.DIRECT


async def test_direct_message_to_unknown_agent(router_env) -> None:
    router, _, bus, metrics = router_env
    report = await router.send("a", "ghost", {"text": "hi"})
    assert report.delivered == []
    assert report.dropped == ["ghost"]
    assert bus.pending_size("ghost") == 0
    assert metrics.messages_dropped == 1


async def test_topic_message(router_env) -> None:
    router, _, bus, _ = router_env
    report = await router.send("a", "topic:alarm", {"level": "red"})
    # 发送者 a 也订阅了 alarm，所以同样收到
    assert sorted(report.delivered) == ["a", "b"]
    assert bus.pending_size("a") == 1
    assert bus.pending_size("b") == 1
    assert bus.pending_size("c") == 0


async def test_broadcast_excludes_sender(router_env) -> None:
    router, _, bus, _ = router_env
    report = await router.send("a", "*", {"all": True})
    assert sorted(report.delivered) == ["b", "c"]
    assert bus.pending_size("a") == 0


async def test_queue_overflow_drops_oldest(router_env) -> None:
    router, _, bus, _ = router_env
    for index in range(12):  # queue_size = 10
        await router.send("a", "b", {"i": index})
    assert bus.pending_size("b") == 10
    messages = await bus.drain("b")
    assert messages[0].payload == {"i": 2}  # 最旧的两条被丢弃
    assert messages[-1].payload == {"i": 11}


async def test_direct_message_to_offline_agent_is_queued(router_env) -> None:
    """心跳过期只影响 online 展示，点对点消息仍应入队等它下次拉取。"""
    router, registry, bus, _ = router_env
    registry._agents["b"].last_seen -= 10_000  # noqa: SLF001 - 测试用
    await registry.mark_stale()
    assert registry.get("b").online is False

    report = await router.send("a", "b", {"queued": True})
    assert report.delivered == ["b"]
    assert report.dropped == []
    assert bus.pending_size("b") == 1


async def test_sink_receives_messages(router_env) -> None:
    router, _, bus, _ = router_env
    import asyncio

    queue: asyncio.Queue = asyncio.Queue(maxsize=5)
    await bus.attach_sink("b", queue)
    await router.send("a", "b", {"push": True})
    envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert envelope.payload == {"push": True}
    await bus.detach_sink("b", queue)
