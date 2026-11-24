"""数据模型。

这里的 Pydantic 模型同时用于：MCP 工具的入参校验、管理接口的响应序列化、
以及集线器内部状态的快照。字段命名保持 snake_case，与 Python 侧一致。
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Agent 标识：字母 / 数字 / 下划线 / 点 / 冒号 / 连字符，长度 1-64
AGENT_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
# 主题名：与 Agent 标识类似，但允许斜杠（如 monitor/alarm）
TOPIC_PATTERN = r"^[A-Za-z0-9_./:-]{1,128}$"


class AgentStatus(str, Enum):
    """Agent 运行状态（由 Agent 自报，集线器只做透传）。"""

    ONLINE = "online"
    BUSY = "busy"
    OFFLINE = "offline"


class MessageType(str, Enum):
    """消息投递类型。"""

    DIRECT = "direct"      # 点对点：target 为 agent_id
    TOPIC = "topic"        # 主题订阅：topic 字段必填
    BROADCAST = "broadcast"  # 广播：target 为 "*"


class AgentRegistration(BaseModel):
    """Agent 注册请求。"""

    agent_id: str = Field(pattern=AGENT_ID_PATTERN, description="Agent 唯一标识")
    name: str | None = Field(default=None, max_length=64, description="显示名")
    version: str | None = Field(default=None, max_length=32, description="Agent 版本号")
    capabilities: list[str] = Field(default_factory=list, description="能力标签，如 [\"report\", \"ocr\"]")
    topics: list[str] = Field(default_factory=list, description="订阅的主题列表")
    metadata: dict[str, Any] = Field(default_factory=dict, description="自定义元数据（落库/展示用）")
    status: AgentStatus = Field(default=AgentStatus.ONLINE, description="初始状态")

    @field_validator("topics")
    @classmethod
    def _check_topics(cls, value: list[str]) -> list[str]:
        for topic in value:
            if not topic or len(topic) > 128:
                raise ValueError(f"非法主题: {topic!r}")
        return value


class AgentStats(BaseModel):
    """单个 Agent 的收发统计。"""

    sent: int = 0
    received: int = 0
    dropped: int = 0
    errors: int = 0


class AgentView(BaseModel):
    """Agent 对外展示视图（管理接口 / 工具返回）。"""

    agent_id: str
    name: str | None = None
    version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: AgentStatus = AgentStatus.OFFLINE
    online: bool = False
    registered_at: float = 0.0
    last_seen: float = 0.0
    last_seen_ago: float = 0.0
    stats: AgentStats = Field(default_factory=AgentStats)


class Envelope(BaseModel):
    """集线器内部与推送使用的消息信封。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = Field(default_factory=time.time)
    sender: str = Field(description="发送方 agent_id")
    target: str = Field(default="*", description="目标 agent_id / * / topic:<name>")
    message_type: MessageType = MessageType.DIRECT
    topic: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict, description="业务负载，结构由 Agent 约定")
    reply_to: str | None = Field(default=None, description="若为回复，填原消息 id")


class DeliveryReport(BaseModel):
    """一次发送的投递结果。"""

    message_id: str
    delivered: list[str] = Field(default_factory=list, description="已投递的 agent_id")
    dropped: list[str] = Field(default_factory=list, description="目标不存在 / 已离线的 agent_id")
    queued: int = Field(default=0, description="本次写入的队列条目总数")


class HubStats(BaseModel):
    """集线器整体统计（管理接口返回）。"""

    uptime_seconds: float
    agents_total: int
    agents_online: int
    messages_sent: int
    messages_delivered: int
    messages_dropped: int
    sessions_active: int
    queue_pending: int


class HubHealth(BaseModel):
    """健康检查响应。"""

    status: Literal["ok"] = "ok"
    version: str
    uptime_seconds: float
    agents_online: int
    agents_total: int
