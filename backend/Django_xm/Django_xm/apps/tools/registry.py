"""扩展工具注册表

允许高层 app（如 ``agent_hub``）向 ``tools`` 注册工具，而不违反分层约束
（高层 → 低层是允许的，低层 → 高层不允许）。

设计动机（Task 15.1）：
    子代理创建工具 ``spawn_sub_agent`` 原位于 ``tools/langchain/agent.py``，
    但其调用 ``agent_hub.create``，违反 ``tools → agent_hub`` 分层。
    迁入 ``agent_hub/tools/`` 后，需通过注册表让 ``tools.get_all_tools()``
    仍能发现该工具。

使用方式：
    # agent_hub/apps.py ready() 中注册（在 agent_hub app 内）
    from Django_xm.apps.tools.registry import register_extension_tools
    from .tools import get_agent_tools  # agent_hub app 内的相对导入
    register_extension_tools(get_agent_tools())

    # tools/__init__.py 中查询
    from .registry import get_extension_tools
    tools.extend(get_extension_tools())
"""

from __future__ import annotations

import logging
import threading

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

_extension_tools: list[BaseTool] = []
_extension_tool_names: set = set()
_lock = threading.Lock()


def register_extension_tools(tools: list[BaseTool]) -> None:
    """注册扩展工具到全局注册表。

    幂等：同名工具不会重复注册。

    Args:
        tools: 要注册的工具列表
    """
    with _lock:
        for tool in tools:
            name = getattr(tool, "name", None)
            if name is None:
                logger.warning(f"跳过无名扩展工具: {tool}")
                continue
            if name in _extension_tool_names:
                logger.debug(f"扩展工具已注册，跳过: {name}")
                continue
            _extension_tools.append(tool)
            _extension_tool_names.add(name)
            logger.debug(f"扩展工具已注册: {name}")


def get_extension_tools() -> list[BaseTool]:
    """获取所有已注册的扩展工具。

    返回新列表，调用方可安全修改。
    """
    with _lock:
        return list(_extension_tools)


def clear_extension_tools() -> None:
    """清空注册表（仅供测试使用）。"""
    with _lock:
        _extension_tools.clear()
        _extension_tool_names.clear()
