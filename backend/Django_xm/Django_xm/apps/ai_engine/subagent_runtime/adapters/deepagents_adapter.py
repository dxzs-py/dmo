"""DeepAgents 适配器。

6.md D-1/D-6：deepagents 的 ``task`` 工具内部改走 ``SubAgentRuntime.spawn`` 后，
子代理不再依赖 deepagents 原生同 graph spawn（interrupt 冒泡父 graph），而是
通过 ``agent_hub.create`` 创建独立 graph + 独立 thread_id + 独立 checkpoint。

因此子代理执行机制与 LangGraphAdapter 完全一致（独立 graph 的 astream / state.next /
Command(resume)），本适配器复用其实现；deepagents 特有差异（backend / work_dir /
skills）由上层 spawn 工具在构造 AgentConfig 时处理，不在适配器层重复。
"""

from __future__ import annotations

from Django_xm.apps.ai_engine.subagent_runtime.adapters.langgraph_adapter import LangGraphAdapter


class DeepAgentsAdapter(LangGraphAdapter):
    """对接 DeepAgents 编排的子代理适配器（复用 LangGraph 独立 graph 执行机制）。"""

    # 执行机制与 LangGraphAdapter 完全一致，无需覆写。
    # 若未来 deepagents 恢复同 graph spawn 语义，再在此处覆写 spawn/resume 映射框架私有字段。
