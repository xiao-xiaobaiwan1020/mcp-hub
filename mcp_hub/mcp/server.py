"""MCP HTTP 端点（Streamable HTTP）。

路由：

- ``POST /mcp``    JSON-RPC 请求入口（initialize / tools/list / tools/call / ping …）
- ``GET  /mcp``    SSE 长连接，服务端通过它主动推送消息
- ``DELETE /mcp``  结束会话

会话通过 ``Mcp-Session-Id`` 头传递；``GET /mcp`` 也支持 ``?agent_id=xxx`` 直接绑定 Agent，
便于用浏览器 / curl 快速观察某个 Agent 的实时消息。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .protocol import (
    INVALID_REQUEST,
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SERVER_NAME,
    SERVER_VERSION,
    JsonRpcError,
    failure,
    negotiate_version,
    notification,
    sse_comment,
    sse_event,
    success,
)
from .tools import TOOLS
from ..hub import Hub
from ..logging_setup import get_logger

log = get_logger("mcp_hub.mcp.server")

SESSION_HEADER = "Mcp-Session-Id"


def build_mcp_router(hub: Hub) -> APIRouter:
    """构造 MCP 协议路由。"""
    router = APIRouter()

    async def _dispatch(request: Request, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        """处理单条 JSON-RPC 报文，返回 (响应, 新建会话 id)。"""
        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}

        if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
            raise JsonRpcError(INVALID_REQUEST, "invalid JSON-RPC request")

        session_id = request.headers.get(SESSION_HEADER) or request.query_params.get("session")
        session = hub.sessions.get(session_id)
        new_session_id: str | None = None

        # ---------------------------------------------------------- 生命周期
        if method == "initialize":
            if session is None:
                session = hub.sessions.create()
                new_session_id = session.session_id
            version = negotiate_version(params.get("protocolVersion"))
            result = {
                "protocolVersion": version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "logging": {},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "多 Agent 消息集线器。先用 hub_register 注册（会绑定当前会话），"
                    "再定期 hub_heartbeat；收发消息用 hub_send / hub_fetch_messages，"
                    "也可以用 GET /mcp 建立 SSE 长连接实时接收。"
                ),
            }
            return success(request_id, result), new_session_id

        if method in ("notifications/initialized", "notifications/cancelled"):
            return None, None

        # ---------------------------------------------------------- 工具
        if method == "tools/list":
            return success(request_id, {"tools": TOOLS}), None

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                raise JsonRpcError(INVALID_REQUEST, "tools/call 缺少 name")
            session_agent = session.agent_id if session is not None else None
            result = await hub.tools.call(name, arguments, session_agent)
            # 注册成功后把当前会话绑定到该 Agent，后续工具即可省略 agent_id
            if name == "hub_register" and session is not None and not result.get("isError"):
                agent_id = arguments.get("agent_id")
                if isinstance(agent_id, str):
                    hub.sessions.bind_agent(session.session_id, agent_id)
            return success(request_id, result), None

        if method == "ping":
            return success(request_id, {}), None

        if method == "resources/list":
            return success(request_id, {"resources": []}), None

        if method == "prompts/list":
            return success(request_id, {"prompts": []}), None

        raise JsonRpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    @router.post("/mcp")
    async def mcp_post(request: Request) -> Response:
        """JSON-RPC 请求入口。"""
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(failure(None, PARSE_ERROR, "parse error"), status_code=400)

        # 批量请求（JSON-RPC 2.0 batch）
        if isinstance(payload, list):
            responses = []
            session_id: str | None = None
            for item in payload:
                try:
                    response, created = await _dispatch(request, item)
                except JsonRpcError as exc:
                    response, created = failure(item.get("id"), exc.code, exc.message, exc.data), None
                if response is not None:
                    responses.append(response)
                session_id = session_id or created
            headers = {SESSION_HEADER: session_id} if session_id else {}
            return JSONResponse(responses, headers=headers)

        # 单条请求
        try:
            response, created = await _dispatch(request, payload)
        except JsonRpcError as exc:
            return JSONResponse(failure(payload.get("id"), exc.code, exc.message, exc.data))
        except Exception as exc:  # noqa: BLE001 - 兜底，避免 500 破坏协议
            log.exception("mcp dispatch failed")
            return JSONResponse(failure(payload.get("id"), INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))

        if response is None:  # 通知，无需响应体
            return Response(status_code=202)
        headers = {SESSION_HEADER: created} if created else {}
        return JSONResponse(response, headers=headers)

    @router.get("/mcp")
    async def mcp_stream(request: Request) -> Response:
        """SSE 长连接：接收服务端推送的消息。"""
        session_id = request.headers.get(SESSION_HEADER) or request.query_params.get("session")
        session = hub.sessions.get(session_id)
        created = False
        if session is None:
            session = hub.sessions.create()
            created = True
        agent_id = request.query_params.get("agent_id") or session.agent_id
        if agent_id:
            hub.sessions.bind_agent(session.session_id, agent_id)
            await hub.bus.attach_sink(agent_id, session.push_queue)
            session.sse_connected = True
            hub.metrics.sse_connections += 1

        async def event_stream():
            try:
                # 首帧告知会话 id，方便纯 HTTP 客户端拿到
                yield sse_event(notification("notifications/message", {
                    "level": "info", "logger": "mcp-hub",
                    "data": {"type": "endpoint", "session_id": session.session_id,
                             "agent_id": agent_id, "protocolVersion": "2025-06-18"},
                }))
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        envelope = await asyncio.wait_for(session.push_queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield sse_comment("keep-alive")
                        continue
                    # 使用 MCP 标准 logging 通知承载消息信封
                    yield sse_event(notification("notifications/message", {
                        "level": "info", "logger": "mcp-hub",
                        "data": envelope.model_dump(mode="json"),
                    }))
            finally:
                if agent_id:
                    await hub.bus.detach_sink(agent_id, session.push_queue)
                    session.sse_connected = False
                    hub.metrics.sse_connections = max(0, hub.metrics.sse_connections - 1)

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            SESSION_HEADER: session.session_id,
        }
        if created:
            headers["X-Session-Created"] = "1"
        return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)

    @router.delete("/mcp")
    async def mcp_delete(request: Request) -> Response:
        """结束会话。"""
        session_id = request.headers.get(SESSION_HEADER) or request.query_params.get("session")
        removed = hub.sessions.remove(session_id)
        return JSONResponse({"ok": True, "session_removed": removed})

    return router


__all__ = ["build_mcp_router", "SESSION_HEADER"]
