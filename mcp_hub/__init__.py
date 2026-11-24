"""MCP 消息集线器（mcp-hub）。

多 Agent 注册中心 + 消息路由 + 心跳检测，基于 MCP 标准协议（JSON-RPC 2.0 / Streamable HTTP）。

包结构::

    mcp_hub/
        config.py        配置（环境变量 / 默认值）
        logging_setup.py 日志初始化
        models.py        数据模型（Agent、消息信封、投递报告）
        registry.py      Agent 注册表（注册 / 心跳 / 在线判定 / 主题订阅）
        bus.py           消息总线（每个 Agent 的待投递队列 + SSE sink）
        router.py        路由（点对点 / 主题 / 广播）
        metrics.py       运行指标（Prometheus 文本输出）
        events.py        事件分发（Webhook，供监测平台订阅）
        hub.py           服务容器与生命周期
        mcp/             MCP 协议层（协议常量、会话、工具、HTTP 端点）
        api/             管理 REST 接口 + FastAPI 应用装配
        sdk/             Agent 侧客户端封装
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
