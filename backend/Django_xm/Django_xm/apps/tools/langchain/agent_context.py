"""
子 Agent 工具上下文管理器（按会话隔离）

在主 Agent 执行期间，保存当前会话的工具配置（工具名列表、联网/MCP 标志），
供 ``spawn_sub_agent`` 工具读取，实现子 Agent 继承父 Agent 的工具集。

典型流程：
1. 主 Agent 开始执行 → set_parent_tool_context(thread_id, tools, config)
2. ``spawn_sub_agent`` 被调用 → get_parent_tool_context(thread_id) → 继承工具集
3. 主 Agent 执行结束 → clear_parent_tool_context(thread_id)

并发安全（根因修复）：上下文按 ``thread_id``（research task id / chat session id）
维度隔离存储，多会话/多任务并发执行时互不覆盖、互不清空。
此前为进程级单一全局存储，任意会话结束时 clear 会清空其他会话的父上下文，
导致并发场景下 spawn 子代理读不到父工具集（如深度研究子代理缺 shell_exec）。
"""

import threading
from typing import Any

from langchain_core.tools import BaseTool

_context_lock = threading.Lock()
# thread_id -> {tool_names, mcp_servers, config}
_context_storage: dict[str, dict[str, Any]] = {}

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


def set_parent_tool_context(
    thread_id: str,
    tools: list[BaseTool],
    config: dict[str, Any] | None = None,
) -> None:
    tool_names = [t.name for t in tools]
    mcp_servers = set()
    for t in tools:
        meta = getattr(t, "metadata", None) or {}
        server = meta.get("mcp_server_name", "")
        if server:
            mcp_servers.add(server)

    with _context_lock:
        _context_storage[thread_id] = {
            "tool_names": tool_names,
            "mcp_servers": list(mcp_servers),
            "config": config or {},
        }


def get_parent_tool_context(thread_id: str) -> dict[str, Any]:
    with _context_lock:
        ctx = _context_storage.get(thread_id, {})
        return {
            "tool_names": list(ctx.get("tool_names", [])),
            "mcp_servers": list(ctx.get("mcp_servers", [])),
            "config": dict(ctx.get("config", {})),
        }


def clear_parent_tool_context(thread_id: str) -> None:
    with _context_lock:
        _context_storage.pop(thread_id, None)
