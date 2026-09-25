"""工具调用状态管理与失败检测。

集中处理工具执行结果的状态检测：
- ``_detect_tool_error``：检测 ToolMessage content 是否为错误
- ``_detect_tool_timeout``：检测审批超时（content 含"审批超时"）
- ``_detect_tool_rejected``：检测用户拒绝（content 含"用户已拒绝"）
"""

import logging
from typing import Any

from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


def _detect_tool_error(result_content: str | list[Any]) -> bool:
    if not result_content or not isinstance(result_content, str):
        return False
    error_prefixes = ["错误：", "Error:", "ERROR:", "FAILED", "失败:", "异常:", "Exception:"]
    return any(result_content.strip().startswith(prefix) for prefix in error_prefixes)


def _detect_tool_timeout(message: ToolMessage) -> bool:
    """检测 ToolMessage 是否表示审批超时。

    通过 content 中包含 "审批超时" 关键字判断，用于触发 TOOL_CALL_TIMEOUT 终态事件。
    content 非 str 时返回 False（健壮性，兼容 list/None 等异常 content 类型）。

    Args:
        message: ToolMessage 实例（langchain_core.messages.ToolMessage）

    Returns:
        bool: True 表示工具因审批超时失败
    """
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return False
    return "审批超时" in content


def _detect_tool_rejected(message: ToolMessage) -> bool:
    """检测 ToolMessage 是否表示用户已拒绝。

    通过 content 中包含 "用户已拒绝" 关键字判断，用于触发 TOOL_CALL_REJECTED 终态事件。
    content 非 str 时返回 False（健壮性，兼容 list/None 等异常 content 类型）。

    Args:
        message: ToolMessage 实例（langchain_core.messages.ToolMessage）

    Returns:
        bool: True 表示工具被用户拒绝
    """
    content = getattr(message, "content", None)
    if not isinstance(content, str):
        return False
    return "用户已拒绝" in content

