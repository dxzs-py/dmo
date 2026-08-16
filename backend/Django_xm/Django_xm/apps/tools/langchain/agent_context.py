"""
子 Agent 工具上下文管理器

在主 Agent 执行期间，保存当前会话的工具配置（工具名列表、联网/MCP 标志），
供 ``spawn_sub_agent`` 工具读取，实现子 Agent 继承父 Agent 的工具集。

典型流程：
1. 主 Agent 开始执行 → set_parent_tool_context(tools, config)
2. ``spawn_sub_agent`` 被调用 → get_parent_tool_context() → 继承工具集 / 深度检测
3. 主 Agent 执行结束 → clear_parent_tool_context()

递归深度控制：
- 通过 agent_depth 追踪当前嵌套层级
- 最大深度可配置：settings.AGENT_MAX_DEPTH（环境变量 AGENT_MAX_DEPTH），
  默认 3（主代理=0，子代理=1，孙代理=2，曾孙代理=3）
- 超过最大深度时拒绝派生，阻止无限嵌套
"""

import threading
from typing import Any

from langchain_core.tools import BaseTool

_context_lock = threading.Lock()
_context_storage: dict[str, Any] = {}

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
        _context_storage["tool_names"] = tool_names
        _context_storage["mcp_servers"] = list(mcp_servers)
        _context_storage["config"] = config or {}
        # 初始化深度：主代理为 0
        if "agent_depth" not in _context_storage:
            _context_storage["agent_depth"] = 0


def get_parent_tool_context() -> dict[str, Any]:
    with _context_lock:
        return {
            "tool_names": list(_context_storage.get("tool_names", [])),
            "mcp_servers": list(_context_storage.get("mcp_servers", [])),
            "config": dict(_context_storage.get("config", {})),
            "agent_depth": _context_storage.get("agent_depth", 0),
        }


def has_parent_tool_context() -> bool:
    with _context_lock:
        return bool(_context_storage.get("tool_names"))


def get_agent_depth() -> int:
    """获取当前代理嵌套深度"""
    with _context_lock:
        return _context_storage.get("agent_depth", 0)


def increment_agent_depth() -> int:
    """递增代理深度并返回新值，用于子代理启动时设置"""
    with _context_lock:
        current = _context_storage.get("agent_depth", 0)
        new_depth = current + 1
        _context_storage["agent_depth"] = new_depth
        return new_depth


def decrement_agent_depth() -> int:
    """递减代理深度并返回新值，用于子代理结束时恢复"""
    with _context_lock:
        current = _context_storage.get("agent_depth", 0)
        new_depth = max(0, current - 1)
        _context_storage["agent_depth"] = new_depth
        return new_depth


def is_max_depth_reached() -> bool:
    """检查是否已达到最大嵌套深度"""
    with _context_lock:
        return _context_storage.get("agent_depth", 0) >= get_max_agent_depth()


def clear_parent_tool_context() -> None:
    with _context_lock:
        _context_storage.clear()
