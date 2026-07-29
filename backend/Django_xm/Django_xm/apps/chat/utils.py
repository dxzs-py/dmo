"""
聊天视图的工具函数
包含消息处理、文本处理等辅助函数
"""

import json
import re

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


def _needs_completion(text: str) -> bool:
    """
    判断文本是否需要补充完整

    Args:
        text: 需要检查的文本

    Returns:
        bool: 是否需要补充
    """
    if not text:
        return True
    t = text.strip()
    if len(t) < 30:
        return True
    # 检查是否以句子结尾标点结束（包括 emoji、中文标点、英文标点）
    sentence_endings = [
        "。",
        "！",
        "？",
        ".",
        "!",
        "?",
        "」",
        "』",
        "）",
        ")",
        "】",
        "]",
        "😊",
        "👍",
        "🎉",
        "✨",
        "💡",
        "📝",
        "🔍",
        "🧮",  # 常见结尾 emoji
    ]
    # 也接受以 emoji 结尾（Unicode emoji 范围）
    import unicodedata

    last_char = t[-1]
    if any(t.endswith(p) for p in sentence_endings):
        return False
    # 检查最后一个字符是否是 emoji
    try:
        if unicodedata.category(last_char).startswith("So"):  # Symbol, Other (emoji)
            return False
    except (ValueError, TypeError):
        pass
    return True


def _lcp_len(a: str, b: str) -> int:
    """
    计算两个字符串的最长公共前缀长度

    Args:
        a: 第一个字符串
        b: 第二个字符串

    Returns:
        int: 最长公共前缀长度
    """
    i = 0
    for ca, cb in zip(a, b, strict=False):
        if ca != cb:
            break
        i += 1
    return i


def convert_chat_history(messages: list[dict]) -> list:
    """
    将 API 的消息格式转换为 LangChain 的消息格式

    Args:
        messages: API 消息列表（字典列表）

    Returns:
        List: LangChain 消息列表
    """
    if not messages:
        return []

    langchain_messages = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        attachment_ids = msg.get("attachment_ids") or []

        if role == "user" and attachment_ids:
            content = _inject_attachment_content(content, attachment_ids)

        if role == "user":
            langchain_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            ai_kwargs = {}
            reasoning_content = msg.get("reasoning_content")
            if reasoning_content and isinstance(reasoning_content, str) and reasoning_content.strip():
                ai_kwargs["additional_kwargs"] = {"reasoning_content": reasoning_content}
            langchain_messages.append(AIMessage(content=content, **ai_kwargs))
        elif role == "system":
            langchain_messages.append(SystemMessage(content=content))

    return langchain_messages


def _inject_attachment_content(user_message: str, attachment_ids: list[int]) -> str:
    try:
        from Django_xm.apps.attachments.services.cross_app import get_attachment_service

        att_svc = get_attachment_service()
        result = att_svc.build_user_content(user_message, attachment_ids)
        if result["type"] == "text":
            return result["content"]
        if result["type"] == "multimodal":
            parts = []
            for part in result["content"]:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    parts.append(part)
            return "\n".join(parts) if parts else user_message
        return user_message
    except Exception:
        return user_message


def extract_suggestions(raw: str) -> list[str]:
    """
    从原始文本中提取建议问题列表

    Args:
        raw: 原始文本（可能包含JSON数组）

    Returns:
        List[str]: 提取的建议问题列表
    """
    suggestions: list[str] = []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            suggestions = [str(x) for x in parsed if isinstance(x, (str, int, float))]
            suggestions = [s for s in suggestions if s.strip()][:4]
    except Exception:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                parsed2 = json.loads(m.group(0))
                if isinstance(parsed2, list):
                    suggestions = [str(x) for x in parsed2 if isinstance(x, (str, int, float))]
                    suggestions = [s for s in suggestions if s.strip()][:4]
            except Exception:
                suggestions = []
    return suggestions
