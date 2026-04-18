"""
DuckDuckGo 搜索工具
无需 API Key 的联网搜索方案，作为 Tavily 的备选
"""

import httpx
import re
import json
from typing import Optional, List
from langchain_core.tools import tool

import logging

logger = logging.getLogger(__name__)

try:
    from langchain_community.tools import DuckDuckGoSearchResults as DuckDuckGoSearch
    HAS_DUCKDUCKGO_TOOL = True
    logger.info("✅ langchain-community DuckDuckGo 搜索工具可用")
except ImportError:
    HAS_DUCKDUCKGO_TOOL = False
    logger.warning("⚠️ DuckDuckGo 搜索工具未安装，请安装: pip install duckduckgo-search")


def _search_via_ddgs(query: str, max_results: int = 5) -> str:
    try:
        from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            search_results = list(ddgs.text(query, max_results=max_results))

        if not search_results:
            return "未找到相关结果"

        formatted = []
        for i, result in enumerate(search_results, 1):
            title = result.get("title", "无标题")
            url = result.get("href", "")
            content = result.get("body", "")[:300]

            formatted.append(
                f"{i}. {title}\n   URL: {url}\n   摘要: {content}"
            )

        output = "搜索结果：\n\n" + "\n\n".join(formatted)
        logger.info(f"🔍 DuckDuckGo 搜索完成，返回 {len(search_results)} 条结果")
        return output

    except ImportError:
        raise ValueError("DuckDuckGo 搜索需要安装: pip install duckduckgo-search")
    except Exception as e:
        raise RuntimeError(f"DuckDuckGo 搜索失败: {str(e)}")


def _search_via_langchain(query: str, max_results: int = 5) -> str:
    if not HAS_DUCKDUCKGO_TOOL:
        raise ValueError("DuckDuckGo 搜索工具未安装: pip install duckduckgo-search")

    search = DuckDuckGoSearch(max_results=max_results)
    result = search.invoke(query)

    if isinstance(result, str):
        return result

    if isinstance(result, list):
        formatted = []
        for i, item in enumerate(result, 1):
            if isinstance(item, dict):
                title = item.get("title", "无标题")
                url = item.get("link", item.get("href", ""))
                content = item.get("snippet", item.get("content", ""))[:300]
                formatted.append(f"{i}. {title}\n   URL: {url}\n   摘要: {content}")
            else:
                formatted.append(f"{i}. {item}")
        return "搜索结果：\n\n" + "\n\n".join(formatted)

    return str(result)


@tool
def duckduckgo_search(query: str, max_results: int = 5) -> str:
    """
    使用 DuckDuckGo 搜索互联网获取信息（无需 API Key）

    当需要搜索最新信息、新闻、技术更新等时使用此工具。
    这是联网搜索的免费方案，不需要任何 API Key。

    Args:
        query: 搜索查询关键词
        max_results: 最大返回结果数，默认5条

    Returns:
        搜索结果列表，包含标题、链接和摘要

    Example:
        >>> duckduckgo_search("Python 3.12 新特性")
        '搜索结果：\\n1. ...'
    """
    logger.info(f"🔍 DuckDuckGo 搜索: {query}")

    try:
        return _search_via_ddgs(query, max_results)
    except ImportError:
        logger.warning("⚠️ duckduckgo-search 未安装，尝试使用 langchain-community 工具")
    except Exception as e:
        logger.warning(f"⚠️ DDGS 搜索失败: {e}，尝试 langchain-community 工具")

    try:
        return _search_via_langchain(query, max_results)
    except Exception as e:
        error_msg = f"搜索失败: {str(e)}。请安装 duckduckgo-search: pip install duckduckgo-search"
        logger.error(error_msg)
        return error_msg


def has_duckduckgo_available() -> bool:
    try:
        from duckduckgo_search import DDGS
        return True
    except ImportError:
        return HAS_DUCKDUCKGO_TOOL


def get_duckduckgo_tools():
    if has_duckduckgo_available():
        return [duckduckgo_search]
    logger.warning("⚠️ DuckDuckGo 搜索不可用，请安装: pip install duckduckgo-search")
    return []


DUCKDUCKGO_TOOLS = get_duckduckgo_tools()
