"""
聊天视图的工具函数
包含消息处理、文本处理等辅助函数
"""
import json
import re
from typing import List, Optional
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage


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
    if not any(t.endswith(p) for p in ["。", "！", "？", ".", "!", "?"]):
        return True
    return False


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
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        i += 1
    return i


def convert_chat_history(messages: List[dict]) -> List:
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
        role = msg.get('role', '')
        content = msg.get('content', '')
        if role == 'user':
            langchain_messages.append(HumanMessage(content=content))
        elif role == 'assistant':
            langchain_messages.append(AIMessage(content=content))
        elif role == 'system':
            langchain_messages.append(SystemMessage(content=content))

    return langchain_messages


def extract_suggestions(raw: str) -> List[str]:
    """
    从原始文本中提取建议问题列表
    
    Args:
        raw: 原始文本（可能包含JSON数组）
    
    Returns:
        List[str]: 提取的建议问题列表
    """
    suggestions: List[str] = []
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
