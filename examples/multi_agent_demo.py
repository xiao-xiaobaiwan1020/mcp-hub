"""多 Agent 演示：注册 3 个 Agent，演示点对点 / 主题 / 广播。

先启动集线器::

    python -m mcp_hub --port 8765

再运行本脚本::

    python examples/multi_agent_demo.py --url http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import json

from mcp_hub.sdk import HubClient


def show(title: str, payload: object) -> None:
    """打印带标题的 JSON。"""
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def main() -> None:
    parser = argparse.ArgumentParser(description="MCP 消息集线器多 Agent 演示")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="集线器地址")
    args = parser.parse_args()

    # 三个 Agent：报告、OCR、监测告警
    agents = {
        "agent-report": HubClient(args.url, agent_id="agent-report"),
        "agent-ocr": HubClient(args.url, agent_id="agent-ocr"),
        "agent-monitor": HubClient(args.url, agent_id="agent-monitor"),
    }
    try:
        for name, hub in agents.items():
            await hub.initialize()
            await hub.register(
                name=name,
                capabilities=["report"] if name == "agent-report" else (
                    ["ocr"] if name == "agent-ocr" else ["alarm"]),
                metadata={"team": "五岳监测", "demo": True},
            )
        # 订阅主题：monitor 由监测 Agent 订阅
        await agents["agent-monitor"].subscribe("monitor/alarm")

        show("在线 Agent", (await agents["agent-report"].list_agents())["agents"])

        # 1) 点对点：报告 Agent 让 OCR Agent 识别一张图
        direct = await agents["agent-report"].send(
            "agent-ocr", {"task": "ocr", "file": "monitor-20261124.png"},
        )
        show("点对点投递", direct)

        # 2) 主题：监测 Agent 发布告警
        topic = await agents["agent-monitor"].send(
            "topic:monitor/alarm", {"level": "red", "point": "K12+300", "value": 18.6},
        )
        show("主题投递", topic)

        # 3) 广播
        broadcast = await agents["agent-report"].send("*", {"notice": "日报已生成"})
        show("广播投递", broadcast)

        # 各 Agent 拉取自己的收件箱
        for name, hub in agents.items():
            inbox = await hub.fetch_messages()
            show(f"{name} 收件箱", inbox["messages"])
    finally:
        for hub in agents.values():
            await hub.close()


if __name__ == "__main__":
    asyncio.run(main())
