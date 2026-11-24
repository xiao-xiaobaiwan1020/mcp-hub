"""集线器对外暴露的 MCP 工具。

工具列表（``tools/list`` 返回）：

=====================  ============================================
工具                    作用
=====================  ============================================
``hub_register``       注册 / 更新 Agent，并把当前 MCP 会话绑定到该 Agent
``hub_heartbeat``      刷新心跳（可同时上报 online / busy 状态）
``hub_unregister``     主动注销
``hub_send``           发送消息（点对点 / 主题 / 广播）
``hub_list_agents``    查看在线 Agent
``hub_fetch_messages`` 拉取待投递消息（没有 SSE 连接时使用）
``hub_subscribe``      订阅主题
``hub_unsubscribe``    取消订阅主题
=====================  ============================================
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ..bus import MessageBus
from ..config import HubConfig
from ..events import EventDispatcher
from ..logging_setup import get_logger
from ..metrics import Metrics
from ..models import AgentRegistration, AgentStatus, MessageType
from ..registry import AgentRegistry
from ..router import Router
from .protocol import INVALID_PARAMS, JsonRpcError, tool_result

log = get_logger("mcp_hub.mcp.tools")

# 工具 JSON Schema 定义（直接用于 tools/list）
TOOLS: list[dict[str, Any]] = [
    {
        "name": "hub_register",
        "description": "注册或更新一个 Agent，并把当前 MCP 会话绑定到它。重复调用等同更新。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent 唯一标识（字母数字 _.:-，1-64 位）"},
                "name": {"type": "string", "description": "显示名"},
                "version": {"type": "string", "description": "版本号"},
                "capabilities": {"type": "array", "items": {"type": "string"}, "description": "能力标签"},
                "topics": {"type": "array", "items": {"type": "string"}, "description": "订阅的主题"},
                "metadata": {"type": "object", "description": "自定义元数据"},
                "status": {"type": "string", "enum": ["online", "busy"], "description": "初始状态"},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "hub_heartbeat",
        "description": "刷新心跳；超过 heartbeat_timeout 未心跳的 Agent 会被判定离线。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "缺省时使用当前会话绑定的 Agent"},
                "status": {"type": "string", "enum": ["online", "busy"], "description": "顺带上报状态"},
            },
        },
    },
    {
        "name": "hub_unregister",
        "description": "注销 Agent，并丢弃其待投递消息。",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
            "required": ["agent_id"],
        },
    },
    {
        "name": "hub_send",
        "description": "发送消息：target 为 agent_id（点对点）、* / all（广播）或 topic:<名称>（主题）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "目标 Agent / * / topic:<名称>"},
                "payload": {"type": "object", "description": "业务负载，结构由 Agent 自行约定"},
                "topic": {"type": "string", "description": "主题名（与 target=topic:xxx 等价）"},
                "message_type": {"type": "string", "enum": ["direct", "topic", "broadcast"]},
                "reply_to": {"type": "string", "description": "被回复的消息 id"},
            },
            "required": ["target", "payload"],
        },
    },
    {
        "name": "hub_list_agents",
        "description": "列出 Agent 及其在线状态、能力、待投递消息数。",
        "inputSchema": {
            "type": "object",
            "properties": {"online_only": {"type": "boolean", "description": "只看在线"}},
        },
    },
    {
        "name": "hub_fetch_messages",
        "description": "拉取并清除待投递消息（没有 SSE 长连接时使用）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "缺省时使用当前会话绑定的 Agent"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
    },
    {
        "name": "hub_subscribe",
        "description": "订阅一个主题，之后发往该主题的消息都会投递给此 Agent。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "agent_id": {"type": "string", "description": "缺省时使用当前会话绑定的 Agent"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "hub_unsubscribe",
        "description": "取消订阅主题。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "agent_id": {"type": "string", "description": "缺省时使用当前会话绑定的 Agent"},
            },
            "required": ["topic"],
        },
    },
]


class HubTools:
    """MCP 工具实现（无状态，所有状态都在 registry / bus / session 里）。"""

    def __init__(self, registry: AgentRegistry, router: Router, bus: MessageBus,
                 config: HubConfig, metrics: Metrics, events: EventDispatcher) -> None:
        self._registry = registry
        self._router = router
        self._bus = bus
        self._config = config
        self._metrics = metrics
        self._events = events
        self._handlers = {
            "hub_register": self._register,
            "hub_heartbeat": self._heartbeat,
            "hub_unregister": self._unregister,
            "hub_send": self._send,
            "hub_list_agents": self._list_agents,
            "hub_fetch_messages": self._fetch_messages,
            "hub_subscribe": self._subscribe,
            "hub_unsubscribe": self._unsubscribe,
        }

    async def call(self, name: str, arguments: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        """执行一个工具调用，返回 MCP 工具结果（``content`` / ``isError``）。"""
        handler = self._handlers.get(name)
        if handler is None:
            raise JsonRpcError(INVALID_PARAMS, f"unknown tool: {name}")
        try:
            return await handler(arguments, session_agent)
        except ValidationError as exc:
            return tool_result({"error": "参数校验失败", "detail": exc.errors(include_url=False)}, is_error=True)
        except JsonRpcError:
            raise
        except Exception as exc:  # noqa: BLE001 - 工具内部错误统一返回 isError
            log.exception("tool %s failed", name)
            self._metrics.errors += 1
            return tool_result({"error": f"{type(exc).__name__}: {exc}"}, is_error=True)

    # ------------------------------------------------------------------ 工具实现
    async def _register(self, args: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        registration = AgentRegistration(**args)
        view, created = await self._registry.register(registration)
        self._metrics.registrations += 1
        event = "agent_registered" if created else "agent_updated"
        self._events.emit(event, view.model_dump(mode="json"))
        return tool_result({
            "agent": view.model_dump(mode="json"),
            "created": created,
            "heartbeat_interval": self._config.heartbeat_interval,
            "heartbeat_timeout": self._config.heartbeat_timeout,
        })

    async def _heartbeat(self, args: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        agent_id = args.get("agent_id") or session_agent
        if not agent_id:
            return tool_result({"error": "agent_id 缺失，且当前会话未绑定 Agent"}, is_error=True)
        status = AgentStatus(args["status"]) if args.get("status") else None
        view = await self._registry.heartbeat(agent_id, status)
        if view is None:
            return tool_result({"error": f"agent not registered: {agent_id}"}, is_error=True)
        self._metrics.heartbeats += 1
        return tool_result({"agent": view.model_dump(mode="json")})

    async def _unregister(self, args: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        agent_id = args.get("agent_id") or session_agent
        if not agent_id:
            return tool_result({"error": "agent_id 缺失"}, is_error=True)
        removed = await self._registry.unregister(agent_id)
        dropped = await self._bus.drop_agent(agent_id) if removed else 0
        if removed:
            self._events.emit("agent_unregistered", {"agent_id": agent_id, "pending_dropped": dropped})
        return tool_result({"agent_id": agent_id, "removed": removed, "pending_dropped": dropped})

    async def _send(self, args: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        sender = session_agent or args.get("sender")
        if not sender:
            return tool_result({"error": "发送前需要先注册（或显式传 sender）"}, is_error=True)
        if self._registry.get(sender) is None:
            return tool_result({"error": f"sender not registered: {sender}"}, is_error=True)
        message_type = MessageType(args["message_type"]) if args.get("message_type") else None
        report = await self._router.send(
            sender=sender,
            target=str(args["target"]),
            payload=dict(args.get("payload") or {}),
            message_type=message_type,
            topic=args.get("topic"),
            reply_to=args.get("reply_to"),
        )
        self._events.emit("message_sent", report.model_dump(mode="json"))
        return tool_result(report.model_dump(mode="json"))

    async def _list_agents(self, args: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        online_only = bool(args.get("online_only", False))
        views = self._registry.list(online_only=online_only)
        payload = []
        for view in views:
            item = view.model_dump(mode="json")
            item["pending_messages"] = self._bus.pending_size(view.agent_id)
            payload.append(item)
        return tool_result({"agents": payload, "total": len(payload)})

    async def _fetch_messages(self, args: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        agent_id = args.get("agent_id") or session_agent
        if not agent_id:
            return tool_result({"error": "agent_id 缺失，且当前会话未绑定 Agent"}, is_error=True)
        limit = int(args.get("limit", 50))
        limit = max(1, min(limit, 200))
        messages = await self._bus.drain(agent_id, limit)
        return tool_result({
            "agent_id": agent_id,
            "count": len(messages),
            "messages": [message.model_dump(mode="json") for message in messages],
        })

    async def _subscribe(self, args: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        agent_id = args.get("agent_id") or session_agent
        topic = str(args.get("topic", ""))
        if not agent_id or not topic:
            return tool_result({"error": "agent_id / topic 缺失"}, is_error=True)
        ok = await self._registry.subscribe(agent_id, topic)
        return tool_result({"agent_id": agent_id, "topic": topic, "subscribed": ok})

    async def _unsubscribe(self, args: dict[str, Any], session_agent: str | None) -> dict[str, Any]:
        agent_id = args.get("agent_id") or session_agent
        topic = str(args.get("topic", ""))
        if not agent_id or not topic:
            return tool_result({"error": "agent_id / topic 缺失"}, is_error=True)
        ok = await self._registry.unsubscribe(agent_id, topic)
        return tool_result({"agent_id": agent_id, "topic": topic, "unsubscribed": ok})


def tools_payload() -> list[dict[str, Any]]:
    """返回 ``tools/list`` 需要的 JSON 结构。"""
    return TOOLS


def dumps(payload: Any) -> str:
    """统一 JSON 序列化（供调试使用）。"""
    return json.dumps(payload, ensure_ascii=False, default=str)
