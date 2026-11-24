"""管理接口（REST）。

供运维 / 监测平台查看集线器状态：

- ``GET  /admin/health``                    健康检查
- ``GET  /admin/agents``                    在线 / 全部 Agent 列表
- ``GET  /admin/agents/{agent_id}``         单个 Agent 详情（含待投递消息数）
- ``GET  /admin/agents/{agent_id}/messages`` 查看待投递消息（不移除）
- ``DELETE /admin/agents/{agent_id}``       强制下线并删除
- ``GET  /admin/stats``                     汇总统计
- ``GET  /admin/metrics``                   Prometheus 文本指标
- ``GET  /admin/config``                    脱敏后的运行配置

配置了 ``MCP_HUB_ADMIN_TOKEN`` 时，所有管理接口都需要请求头 ``X-Admin-Token``。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ..hub import Hub
from ..logging_setup import get_logger
from ..models import AgentStatus

log = get_logger("mcp_hub.api.admin")


def build_admin_router(hub: Hub) -> APIRouter:
    """构造管理接口路由。"""

    async def require_token(
        x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
    ) -> None:
        """可选的静态令牌校验。"""
        expected = hub.config.admin_token
        if expected and x_admin_token != expected:
            raise HTTPException(status_code=401, detail="invalid admin token")

    router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_token)])

    @router.get("/health", summary="健康检查")
    async def health() -> dict[str, Any]:
        return hub.health().model_dump(mode="json")

    @router.get("/agents", summary="Agent 列表")
    async def list_agents(
        online_only: bool = Query(default=False, description="只看在线"),
        status: AgentStatus | None = Query(default=None, description="按状态过滤"),
    ) -> dict[str, Any]:
        views = hub.registry.list(status=status, online_only=online_only)
        items = []
        for view in views:
            item = view.model_dump(mode="json")
            item["pending_messages"] = hub.bus.pending_size(view.agent_id)
            items.append(item)
        _, online = hub.registry.counts()
        return {"total": len(items), "online": online, "agents": items}

    @router.get("/agents/{agent_id}", summary="Agent 详情")
    async def get_agent(agent_id: str) -> dict[str, Any]:
        view = hub.registry.get(agent_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
        item = view.model_dump(mode="json")
        item["pending_messages"] = hub.bus.pending_size(agent_id)
        return item

    @router.get("/agents/{agent_id}/messages", summary="查看待投递消息（不移除）")
    async def peek_messages(agent_id: str, limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
        if hub.registry.get(agent_id) is None:
            raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
        messages = await hub.bus.peek(agent_id, limit)
        return {
            "agent_id": agent_id,
            "pending": hub.bus.pending_size(agent_id),
            "messages": [message.model_dump(mode="json") for message in messages],
        }

    @router.delete("/agents/{agent_id}", summary="强制下线并删除 Agent")
    async def kick_agent(agent_id: str) -> dict[str, Any]:
        removed = await hub.registry.unregister(agent_id)
        dropped = await hub.bus.drop_agent(agent_id) if removed else 0
        if removed:
            hub.events.emit("agent_unregistered", {"agent_id": agent_id, "reason": "admin_kick",
                                                   "pending_dropped": dropped})
            log.warning("agent kicked by admin: %s", agent_id)
        return {"agent_id": agent_id, "removed": removed, "pending_dropped": dropped}

    @router.get("/stats", summary="汇总统计")
    async def stats() -> dict[str, Any]:
        return hub.stats().model_dump(mode="json")

    @router.get("/metrics", summary="Prometheus 指标", response_class=PlainTextResponse)
    async def metrics() -> str:
        total, online = hub.registry.counts()
        return hub.metrics.render_prometheus(
            agents_total=total,
            agents_online=online,
            sessions=hub.sessions.count(),
            queue_pending=hub.bus.total_pending(),
        )

    @router.get("/config", summary="运行配置（脱敏）")
    async def config() -> dict[str, Any]:
        cfg = hub.config
        return {
            "host": cfg.host,
            "port": cfg.port,
            "heartbeat_interval": cfg.heartbeat_interval,
            "heartbeat_timeout": cfg.heartbeat_timeout,
            "offline_retention": cfg.offline_retention,
            "queue_size": cfg.queue_size,
            "admin_token_set": bool(cfg.admin_token),
            "broadcast_include_sender": cfg.broadcast_include_sender,
            "webhook_urls": list(cfg.webhook_urls),
            "log_level": cfg.log_level,
            "log_json": cfg.log_json,
        }

    return router
