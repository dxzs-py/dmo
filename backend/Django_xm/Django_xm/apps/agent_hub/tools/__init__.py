"""Agent Hub 工具集

本包存放与 Agent 生命周期管理相关的工具（创建/执行/列举/清理子代理）。

归属说明（Task 15.1）：
    本模块原位于 ``apps/tools/langchain/agent.py``，但其核心逻辑
    ``AgentRunTool._execute_agent()`` 调用 ``agent_hub.create`` 创建子代理，
    违反 ``tools``（低层）不应依赖 ``agent_hub``（高层）的分层约束。
    子代理管理本质上是 Agent Hub 的职责，故整体迁入 ``agent_hub/tools/``。

依赖方向（迁入后）：
    - ``agent_hub.tools`` → ``tools.langchain.agent_context``（高层 → 低层，正确）
    - ``agent_hub.tools`` → ``agent_hub.create``（同 app 内，正确）
    - ``agent_hub.tools`` → ``context_manager.services``（高层 → 中层，正确）
"""

from .agent_management import (
    AGENT_TOOLS,
    AGENT_TYPES,
    agent_cleanup,
    agent_create,
    agent_list,
    agent_run,
    get_agent_tools,
)

__all__ = [
    "AGENT_TOOLS",
    "AGENT_TYPES",
    "agent_cleanup",
    "agent_create",
    "agent_list",
    "agent_run",
    "get_agent_tools",
]
