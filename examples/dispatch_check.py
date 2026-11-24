"""调度演示：以 DSH 的名义把「监测数据校核」任务经集线器发给校核 Agent。

流程：

1. 以 ``dsd-orchestrator`` 身份注册（代表 DSH 这一侧的调度者）
2. 读取 ``examples/data/k12.json``，组装 ``check.request`` 任务
3. 通过集线器点对点发给 ``agent-check``
4. 轮询自己的收件箱，拿到 ``check.result`` 并打印

运行（先启动集线器和校核 Agent）::

    python examples/dispatch_check.py --url http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mcp_hub.sdk import HubClient

DATA_FILE = Path(__file__).parent / "data" / "k12.json"


async def dispatch(url: str, target: str, timeout: float = 20.0) -> dict:
    """发送任务并等待结果。"""
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    async with HubClient(url, agent_id="dsd-orchestrator") as hub:
        await hub.initialize()
        await hub.register(name="DSH 调度者", capabilities=["orchestrator"],
                           metadata={"role": "orchestrator", "source": "DeepSeek Harness"})
        print(f"[orchestrator] 向 {target} 下发任务：{data['project']}，{len(data['points'])} 个测点")
        report = await hub.send(target, {"action": "check.request", "data": data})
        print(f"[orchestrator] 投递结果：delivered={report['delivered']} dropped={report['dropped']}")

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            inbox = await hub.fetch_messages(limit=20)
            for message in inbox.get("messages", []):
                payload = message.get("payload") or {}
                if payload.get("action") == "check.result":
                    await hub.unregister()
                    return payload["result"]
            await asyncio.sleep(0.3)
        await hub.unregister()
        raise TimeoutError("等待校核结果超时")


def render(result: dict) -> None:
    """打印校核结果。"""
    print(f"\n项目：{result['project']}　报告日期：{result.get('report_date')}")
    print(f"汇总：{result['summary']}\n")
    print(f"{'测点':<28}{'本期':>8}{'累计':>9}{'速率/日':>10}  结论")
    for point in result["points"]:
        print(f"{point['name']:<28}{point['delta_last']:>8}{point['delta_total']:>9}"
              f"{point['rate_per_day']:>10}  {point['level']}")
        for reason in point["reasons"]:
            print(f"    ⚠ {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="监测数据校核调度演示")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--target", default="agent-check")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    result = asyncio.run(dispatch(args.url, args.target, args.timeout))
    render(result)


if __name__ == "__main__":
    main()
