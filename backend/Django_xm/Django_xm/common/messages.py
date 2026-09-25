"""LangChain 消息工具函数。

集中管理 ``BaseMessage.content``（类型为 ``str | list[str | dict]``）的
纯文本提取逻辑，消除全代码库重复的 ``_content_to_str`` 实现。

设计要点：
- LangChain 的 message.content 在多模态场景下为列表结构，
  此处统一拼接为纯文本字符串。
- 元素类型注解使用 ``Any`` 而非 ``str | dict[str, Any]``，
  因为 list 是不变的（invariant），过精确的元素类型会让调用方传入
  ``list[dict[str, str]]`` 等子类型时触发 mypy arg-type 错误。
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage

__all__ = ["content_to_str", "message_text"]


def content_to_str(content: str | Sequence[Any], sep: str = " ") -> str:
    """从 LangChain 消息内容中提取纯文本。

    Args:
        content: ``BaseMessage.content``，可为 ``str`` 或多模态列表。
        sep: 列表元素拼接分隔符。完整消息文本提取用空格（默认），
            流式 token 连续拼接用空串 ``""``（避免在 token 片段间插入空格）。是约定 / 工程规范

    Returns:
        拼接后的纯文本字符串。
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(str(item.get("text", "")))
    return sep.join(parts)


def message_text(message: BaseMessage | None) -> str:
    """从单条消息中提取纯文本，``None`` 返回空串。

    Args:
        message: LangChain 消息对象，可为 ``None``。

    Returns:
        消息内容的纯文本。
    """
    if message is None:
        return ""
    return content_to_str(message.content)
