"""
翻译工具
基于 LLM 实现多语言翻译功能，无需额外 API Key
"""

from typing import Optional
from langchain_core.tools import tool

import logging

logger = logging.getLogger(__name__)

LANGUAGE_MAP = {
    "中文": "Chinese",
    "英语": "English",
    "日语": "Japanese",
    "韩语": "Korean",
    "法语": "French",
    "德语": "German",
    "西班牙语": "Spanish",
    "葡萄牙语": "Portuguese",
    "俄语": "Russian",
    "阿拉伯语": "Arabic",
    "意大利语": "Italian",
    "泰语": "Thai",
    "越南语": "Vietnamese",
    "印尼语": "Indonesian",
    "马来语": "Malay",
    "荷兰语": "Dutch",
    "波兰语": "Polish",
    "土耳其语": "Turkish",
    "印地语": "Hindi",
    "乌克兰语": "Ukrainian",
}


def _normalize_language(lang: str) -> str:
    return LANGUAGE_MAP.get(lang, lang)


@tool
def translate_text(
    text: str,
    target_language: str,
    source_language: Optional[str] = None,
) -> str:
    """
    翻译文本到指定语言

    当用户需要翻译文本、切换对话语言、或用不同语言交流时使用此工具。

    Args:
        text: 要翻译的文本内容
        target_language: 目标语言（如"英语"、"中文"、"日语"、"法语"等）
        source_language: 源语言（可选，不指定则自动检测）

    Returns:
        翻译后的文本

    Example:
        >>> translate_text("你好世界", "英语")
        'Hello World'
        >>> translate_text("Hello World", "中文")
        '你好世界'
    """
    logger.info(f"🌐 翻译请求: target={target_language}, source={source_language or 'auto'}")

    target = _normalize_language(target_language)

    try:
        from Django_xm.apps.core.models import get_chat_model

        model = get_chat_model(temperature=0.3)

        if source_language:
            source = _normalize_language(source_language)
            prompt = (
                f"请将以下{source}文本翻译为{target}。"
                f"只返回翻译结果，不要添加解释或注释。\n\n"
                f"原文：{text}"
            )
        else:
            prompt = (
                f"请将以下文本翻译为{target}。"
                f"自动检测原文语言，只返回翻译结果，不要添加解释或注释。\n\n"
                f"原文：{text}"
            )

        response = model.invoke([{"role": "user", "content": prompt}])
        translated = getattr(response, "content", "")

        if not translated:
            return "翻译失败：未获取到翻译结果"

        logger.info(f"🌐 翻译完成: {len(translated)} 字符")
        return translated.strip()

    except Exception as e:
        error_msg = f"翻译失败: {str(e)}"
        logger.error(error_msg)
        return error_msg


@tool
def detect_language(text: str) -> str:
    """
    检测文本的语言

    当需要识别文本使用的是什么语言时使用此工具。

    Args:
        text: 要检测语言的文本

    Returns:
        检测到的语言名称

    Example:
        >>> detect_language("你好世界")
        '中文'
        >>> detect_language("Hello World")
        '英语'
    """
    logger.info(f"🌐 语言检测请求")

    try:
        from Django_xm.apps.core.models import get_chat_model

        model = get_chat_model(temperature=0.0)

        prompt = (
            "请检测以下文本的语言，只返回语言的中文名称（如：中文、英语、日语、法语等），"
            "不要添加任何其他内容。\n\n"
            f"文本：{text[:500]}"
        )

        response = model.invoke([{"role": "user", "content": prompt}])
        result = getattr(response, "content", "").strip()

        logger.info(f"🌐 语言检测结果: {result}")
        return result if result else "无法检测"

    except Exception as e:
        error_msg = f"语言检测失败: {str(e)}"
        logger.error(error_msg)
        return error_msg


def get_translation_tools():
    return [translate_text, detect_language]


TRANSLATION_TOOLS = get_translation_tools()
