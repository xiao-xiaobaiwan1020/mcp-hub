"""命令行入口：``python -m mcp_hub`` 或安装后的 ``mcp-hub``。"""

from __future__ import annotations

import argparse

import uvicorn

from . import __version__
from .api.app import create_app
from .config import HubConfig
from .logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(
        prog="mcp-hub",
        description="MCP 消息集线器：多 Agent 注册、心跳、路由与消息转发",
    )
    parser.add_argument("--host", default=None, help="监听地址（默认取 MCP_HUB_HOST 或 127.0.0.1）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认 8765）")
    parser.add_argument("--log-level", default=None, help="日志级别：DEBUG/INFO/WARNING/ERROR")
    parser.add_argument("--json-logs", action="store_true", help="输出 JSON 日志")
    parser.add_argument("--version", action="version", version=f"mcp-hub {__version__}")
    return parser


def main() -> None:
    """解析参数并启动服务。"""
    args = build_parser().parse_args()
    config = HubConfig.from_env()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.log_level:
        config.log_level = args.log_level
    if args.json_logs:
        config.log_json = True

    setup_logging(config.log_level, config.log_json)
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port, log_config=None)


if __name__ == "__main__":
    main()
