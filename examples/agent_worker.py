"""常驻 Agent 模板：注册 → 心跳 → SSE 实时接收消息。

可直接改造成真实的监测 Agent：把 ``handle_message`` 换成你的业务处理
（例如：收到 report.request 就生成监测日报并回传路径）。

运行::

    python examples/agent_worker.py --id agent-report --topics report,alarm
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal

from mcp_hub.sdk import HubClient


async def handle_message(agent_id: str, message: dict) -> None:
    """业务处理入口：这里只打印，真实场景换成自己的逻辑。"""
    print(f"[{agent_id}] 收到消息 {json.dumps(message, ensure_ascii=False)}")


async def run(url: str, agent_id: str, topics: list[str]) -> None:
    stop = asyncio.Event()
    async with HubClient(url, agent_id=agent_id) as hub:
        await hub.initialize()
        await hub.register(name=agent_id, topics=topics)
        print(f"[{agent_id}] 已注册，订阅主题：{topics or '无'}")

        heartbeat_task = asyncio.create_task(hub.heartbeat_loop(interval=10.0, stop=stop))
        try:
            async for message in hub.events():
                # SSE 推送：消息实时到达
                await handle_message(agent_id, message)
        finally:
            stop.set()
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            await hub.unregister()


def main() -> None:
    parser = argparse.ArgumentParser(description="常驻 Agent 示例")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--id", default="agent-demo")
    parser.add_argument("--topics", default="", help="逗号分隔的订阅主题")
    args = parser.parse_args()
    topics = [item.strip() for item in args.topics.split(",") if item.strip()]

    loop = asyncio.new_event_loop()
    task = loop.create_task(run(args.url, args.id, topics))
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, task.cancel)
    try:
        loop.run_until_complete(task)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
