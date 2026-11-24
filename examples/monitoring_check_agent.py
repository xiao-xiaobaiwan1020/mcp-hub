"""监测数据校核 Agent（常驻进程）。

职责（确定性计算，不依赖大模型）：
收到 ``check.request`` → 对每个测点计算

- 本期变化量（最新一期 - 上一期）
- 累计变化量（最新一期 - 首期）
- 日均变化速率（累计 / 首末间隔天数）
- 与预警值比较，给出 ``ok / warn / alert`` 与原因

然后把 ``check.result`` 回发给发送方。

运行::

    python examples/monitoring_check_agent.py --url http://127.0.0.1:8765 --id agent-check
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import date

from mcp_hub.sdk import HubClient


def parse_day(text: str) -> date:
    """解析 YYYY-MM-DD。"""
    year, month, day = (int(part) for part in text.split("-"))
    return date(year, month, day)


def check_point(point: dict) -> dict:
    """校核单个测点，返回结构化结果。"""
    readings = sorted(point.get("readings", []), key=lambda item: item["date"])
    if len(readings) < 2:
        return {
            "id": point.get("id"),
            "name": point.get("name"),
            "level": "unknown",
            "reasons": ["观测数据不足两期，无法校核"],
        }

    first, prev, last = readings[0], readings[-2], readings[-1]
    delta_last = round(last["value"] - prev["value"], 4)
    delta_total = round(last["value"] - first["value"], 4)
    days = max(1, (parse_day(last["date"]) - parse_day(first["date"])).days)
    rate = round(delta_total / days, 4)

    warn_rate = float(point.get("warn_rate", 0.5))
    warn_total = float(point.get("warn_total", 15))

    reasons: list[str] = []
    level = "ok"
    if abs(rate) > warn_rate:
        level = "warn"
        reasons.append(f"日均速率 {rate} 超过预警速率 {warn_rate}")
    if abs(delta_total) > warn_total:
        level = "alert"
        reasons.append(f"累计变化 {delta_total} 超过预警值 {warn_total}")

    return {
        "id": point.get("id"),
        "name": point.get("name"),
        "unit": point.get("unit", "mm"),
        "period": {"from": prev["date"], "to": last["date"]},
        "delta_last": delta_last,
        "delta_total": delta_total,
        "rate_per_day": rate,
        "level": level,
        "reasons": reasons,
    }


def check_payload(payload: dict) -> dict:
    """校核整份数据，返回带汇总的结果。"""
    points = [check_point(point) for point in payload.get("points", [])]
    summary = {"points": len(points), "ok": 0, "warn": 0, "alert": 0, "unknown": 0}
    for item in points:
        summary[item["level"]] = summary.get(item["level"], 0) + 1
    return {
        "project": payload.get("project", "未命名项目"),
        "report_date": payload.get("report_date"),
        "generated_at": asyncio.get_event_loop().time(),
        "points": points,
        "summary": summary,
    }


async def handle(agent: HubClient, message: dict) -> None:
    """处理一条任务消息。"""
    payload = message.get("payload") or {}
    action = payload.get("action")
    if action != "check.request":
        return
    result = check_payload(payload.get("data") or payload)
    await agent.send(
        message["sender"],
        {"action": "check.result", "result": result},
        reply_to=message.get("id"),
    )
    print(f"[agent-check] 已回发校核结果给 {message['sender']}：{result['summary']}")


async def run(url: str, agent_id: str) -> None:
    async with HubClient(url, agent_id=agent_id) as hub:
        await hub.initialize()
        await hub.register(name="监测数据校核 Agent", capabilities=["monitoring-check"],
                           topics=["monitoring/check"],
                           metadata={"role": "checker", "deterministic": True})
        print(f"[{agent_id}] 已注册并订阅 monitoring/check")
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(hub.heartbeat_loop(interval=10.0, stop=stop))
        try:
            async for message in hub.events():
                payload = message.get("payload") or {}
                if message.get("message_type") == "topic" or payload.get("action") == "check.request":
                    await handle(hub, message)
        finally:
            stop.set()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await hub.unregister()


def main() -> None:
    parser = argparse.ArgumentParser(description="监测数据校核 Agent")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--id", default="agent-check")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.url, args.id))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
