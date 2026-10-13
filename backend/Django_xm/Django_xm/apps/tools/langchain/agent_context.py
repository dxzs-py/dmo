"""
子 Agent 嵌套深度限制（唯一配置源 settings.AGENT_MAX_DEPTH）

历史形态：本模块曾用进程内全局 dict（``set/get/clear_parent_tool_context``）保存
"父工具上下文"（工具名列表 + 运行配置），供 ``spawn_sub_agent`` 继承父工具集。
该旁路依赖外部注册时机，子代理自身从不注册导致嵌套断链（孙代理回退基础工具集），
且存在注册/清除时序与并发隔离隐患。已根本重构为：工具集与运行配置统一经
LangGraph ``configurable`` 显式传递（主 agent 由 chat_service / research_runner
写入，子代理由 langgraph_adapter._build_configurable 写入），删除全部全局存储。

本模块现仅保留嵌套深度上限读取的兼容 re-export（C14 收敛：常量与判定函数
唯一实现在 ``Django_xm/common/agent_limits.py``），供子代理派生与嵌套中间件共用
同一配置源。
"""

from __future__ import annotations

from Django_xm.common.agent_limits import DEFAULT_MAX_AGENT_DEPTH, get_max_agent_depth

__all__ = ["DEFAULT_MAX_AGENT_DEPTH", "MAX_AGENT_DEPTH", "get_max_agent_depth"]


# 模块级常量保留（首次 import 时求值），兼容既有 import（subagent_support / 测试）；
# 需运行时感知 settings 变更的场景请调用 get_max_agent_depth()。
MAX_AGENT_DEPTH: int = get_max_agent_depth()
