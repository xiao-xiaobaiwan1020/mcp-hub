"""MCP 协议常量与 JSON-RPC 编解码。

MCP（Model Context Protocol）基于 JSON-RPC 2.0。集线器实现的是「Streamable HTTP」
传输：客户端 ``POST /mcp`` 发送请求并在同一响应里拿到结果；服务端主动推送的消息走
``GET /mcp``（SSE 长连接）。
"""

from __future__ import annotations

import json
from typing import Any

# MCP 支持的协议版本（新 → 旧）。协商时沿用客户端请求的版本，不认识则回落到最新。
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_NAME = "mcp-hub"
SERVER_VERSION = "0.1.0"

# JSON-RPC 2.0 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    """可直接转成 JSON-RPC error 响应的业务异常。"""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """构造成功响应。"""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    """构造错误响应。"""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """构造服务端主动推送的通知（无 id）。"""
    return {"jsonrpc": "2.0", "method": method, "params": params}


def sse_event(payload: dict[str, Any], event: str = "message") -> str:
    """把 JSON-RPC 报文包装成 SSE 数据帧。"""
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {body}\n\n"


def sse_comment(text: str = "keep-alive") -> str:
    """SSE 注释行，用于保活（客户端会忽略）。"""
    return f": {text}\n\n"


def negotiate_version(requested: str | None) -> str:
    """协商协议版本：受支持则沿用客户端版本，否则返回服务端最新版本。"""
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


def tool_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    """把任意可序列化对象包装成 MCP ``tools/call`` 结果。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}
