"""
子 Agent 嵌套深度限制（唯一配置源 settings.AGENT_MAX_DEPTH）

历史形态：本模块曾用进程内全局 dict（``set/get/clear_parent_tool_context``）保存
"父工具上下文"（工具名列表 + 运行配置），供 ``spawn_sub_agent`` 继承父工具集。
该旁路依赖外部注册时机，子代理自身从不注册导致嵌套断链（孙代理回退基础工具集），
且存在注册/清除时序与并发隔离隐患。已根本重构为：工具集与运行配置统一经
LangGraph ``configurable`` 显式传递（主 agent 由 chat_service / research_runner
写入，子代理由 langgraph_adapter._build_configurable 写入），删除全部全局存储。

本模块现仅保留嵌套深度上限读取（``get_max_agent_depth``），供子代理派生与
嵌套中间件共用同一配置源。
"""

from __future__ import annotations

# 默认最大子代理嵌套深度（0=主代理, 1=子代理, 2=孙代理, 3=曾孙代理）
_DEFAULT_MAX_AGENT_DEPTH: int = 3


def get_max_agent_depth() -> int:
    """读取最大子代理嵌套深度（lazy 读 settings.AGENT_MAX_DEPTH，兼容非 Django 上下文）。

    Django settings 未配置/读取异常（如独立 unittest 上下文）时回退默认值，
    保证本模块可在无 Django 环境下被引用。
    """
    try:
        from django.conf import settings

        return int(getattr(settings, "AGENT_MAX_DEPTH", _DEFAULT_MAX_AGENT_DEPTH))
    except Exception:
        return _DEFAULT_MAX_AGENT_DEPTH


# 模块级常量保留（首次 import 时求值），兼容既有 import（subagent_support / 测试）；
# 需运行时感知 settings 变更的场景请调用 get_max_agent_depth()。
MAX_AGENT_DEPTH: int = get_max_agent_depth()
