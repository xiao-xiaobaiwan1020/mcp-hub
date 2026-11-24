"""集线器配置。

所有配置均可通过环境变量覆盖（前缀 ``MCP_HUB_``），便于部署到容器 / 监测平台。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(slots=True)
class HubConfig:
    """集线器运行参数。

    默认值面向单机开发；生产环境通常只需覆盖 ``host`` / ``port`` / ``admin_token`` /
    ``webhook_urls``。
    """

    #: 监听地址。对外提供服务时改成 0.0.0.0，并务必配置 admin_token。
    host: str = "127.0.0.1"
    #: 监听端口。
    port: int = 8765
    #: 心跳建议间隔（秒），下发给 Agent 作为参考值。
    heartbeat_interval: float = 10.0
    #: 心跳超时（秒）：超过该时间未收到心跳即判定离线。
    heartbeat_timeout: float = 30.0
    #: Agent 离线后在注册表中保留的时长（秒），到期清理；0 表示永不清理。
    offline_retention: float = 600.0
    #: 每个 Agent 的待投递队列上限，超出时丢弃最旧消息。
    queue_size: int = 200
    #: 管理接口令牌；为空表示不校验（仅建议本机开发使用）。
    admin_token: str | None = None
    #: 广播是否包含发送者自身。
    broadcast_include_sender: bool = False
    #: 监测平台 Webhook 地址列表，集线器会把上线/离线/消息事件 POST 过去。
    webhook_urls: list[str] = field(default_factory=list)
    #: Webhook 请求超时（秒）。
    webhook_timeout: float = 5.0
    #: 日志级别（DEBUG / INFO / WARNING / ERROR）。
    log_level: str = "INFO"
    #: 是否输出 JSON 日志（便于接入 ELK / Loki）。
    log_json: bool = False

    @classmethod
    def from_env(cls) -> "HubConfig":
        """从环境变量构造配置。"""
        token = os.environ.get("MCP_HUB_ADMIN_TOKEN") or None
        return cls(
            host=_env_str("MCP_HUB_HOST", "127.0.0.1"),
            port=_env_int("MCP_HUB_PORT", 8765),
            heartbeat_interval=_env_float("MCP_HUB_HEARTBEAT_INTERVAL", 10.0),
            heartbeat_timeout=_env_float("MCP_HUB_HEARTBEAT_TIMEOUT", 30.0),
            offline_retention=_env_float("MCP_HUB_OFFLINE_RETENTION", 600.0),
            queue_size=_env_int("MCP_HUB_QUEUE_SIZE", 200),
            admin_token=token,
            broadcast_include_sender=_env_str("MCP_HUB_BROADCAST_INCLUDE_SENDER", "0") in ("1", "true", "True"),
            webhook_urls=_env_list("MCP_HUB_WEBHOOK_URLS"),
            webhook_timeout=_env_float("MCP_HUB_WEBHOOK_TIMEOUT", 5.0),
            log_level=_env_str("MCP_HUB_LOG_LEVEL", "INFO").upper(),
            log_json=_env_str("MCP_HUB_LOG_JSON", "0") in ("1", "true", "True"),
        )
