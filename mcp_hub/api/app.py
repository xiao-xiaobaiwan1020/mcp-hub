"""FastAPI 应用装配。

``create_app()`` 是唯一入口：装配 MCP 协议路由 + 管理路由，并在 lifespan 里启停
``Hub``。uvicorn 以工厂模式启动（``--factory``），测试里可以直接拿 ``app`` 走
``httpx.ASGITransport``。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from .. import __version__
from ..config import HubConfig
from ..hub import Hub
from ..mcp.server import SESSION_HEADER, build_mcp_router
from .admin import build_admin_router


def create_app(config: HubConfig | None = None) -> FastAPI:
    """创建 FastAPI 应用。"""
    cfg = config or HubConfig.from_env()
    hub = Hub(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(
        title="MCP 消息集线器",
        version=__version__,
        description="多 Agent 注册、心跳、路由与消息转发（MCP 标准协议 / 异步）",
        lifespan=lifespan,
    )
    app.state.hub = hub

    app.include_router(build_mcp_router(hub))
    app.include_router(build_admin_router(hub))

    @app.get("/", include_in_schema=False)
    async def index() -> dict[str, Any]:
        """根路径：给出关键入口，方便人工排查。"""
        return {
            "service": "mcp-hub",
            "version": __version__,
            "protocol": {"transport": "mcp-streamable-http", "endpoint": "/mcp",
                         "session_header": SESSION_HEADER},
            "admin": {"health": "/admin/health", "agents": "/admin/agents",
                      "stats": "/admin/stats", "metrics": "/admin/metrics"},
            "docs": "/docs",
        }

    return app


__all__ = ["create_app"]
