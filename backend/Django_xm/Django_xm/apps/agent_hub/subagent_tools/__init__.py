"""Agent Hub 子代理工具集

本包存放子代理生命周期相关工具：
- ``spawn_sub_agent``：派发子代理（SubAgentRuntime.spawn 唯一入口，立即返回）。
- ``wait_for_subagent``：父 Agent 业务等待挂起（非审批 interrupt），
  由调度器在子代理终态后唤醒父 Graph 并返回结果。

依赖方向（高层 → 低层）：
    - ``agent_hub.subagent_tools`` → ``ai_engine.subagent_runtime``（子代理唯一入口 + 注册表）
    - ``agent_hub.subagent_tools`` → ``tools``（获取/解析工具集）
    - 工具集与运行配置经 ``configurable`` 显式传递（主 agent 由 chat_service /
      research_runner 写入，子代理由 langgraph_adapter._build_configurable 逐层覆盖）
"""

from .spawn import spawn_sub_agent
from .wait import wait_for_subagent


def get_agent_tools():
    """返回 Agent Hub 注册到 tools 扩展注册表的工具列表。"""
    return [spawn_sub_agent, wait_for_subagent]


AGENT_TOOLS = get_agent_tools()

__all__ = [
    "AGENT_TOOLS",
    "get_agent_tools",
    "spawn_sub_agent",
    "wait_for_subagent",
]
