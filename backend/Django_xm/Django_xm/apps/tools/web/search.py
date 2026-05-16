"""
网络搜索工具
使用 Tavily API 提供网络搜索功能，获取最新信息
"""

from typing import Optional, List, Dict, Any
from langchain_core.tools import tool

import logging

logger = logging.getLogger(__name__)

try:
    from langchain_tavily import TavilySearch
    USING_NEW_TAVILY = True
except ImportError:
    try:
        from langchain_community.tools.tavily_search import TavilySearchResults as TavilySearch
        USING_NEW_TAVILY = False
    except ImportError:
        TavilySearch = None
        USING_NEW_TAVILY = False


def get_tavily_api_key() -> Optional[str]:
    try:
        from Django_xm.apps.ai_engine.config import settings
        return getattr(settings, 'tavily_api_key', None)
    except ImportError:
        import os
        return os.environ.get("TAVILY_API_KEY")


def get_tavily_max_results() -> int:
    try:
        from Django_xm.apps.ai_engine.config import settings
        return getattr(settings, 'tavily_max_results', 5)
    except ImportError:
        import os
        return int(os.environ.get("TAVILY_MAX_RESULTS", "5"))


def create_tavily_search_tool(
    max_results: Optional[int] = None,
    search_depth: str = "advanced",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
):
    if TavilySearch is None:
        raise ValueError("Tavily 搜索工具未安装！请安装: pip install langchain-tavily")

    tavily_api_key = get_tavily_api_key()
    if not tavily_api_key:
        raise ValueError("Tavily API Key 未设置！请在环境变量或 .env 文件中设置 TAVILY_API_KEY")

    max_results = max_results or get_tavily_max_results()

    logger.info(f"🔍 创建 Tavily 搜索工具 (max_results={max_results}, depth={search_depth})")

    tool_kwargs = {
        "max_results": max_results,
        "api_key": tavily_api_key,
    }

    if USING_NEW_TAVILY:
        tool_kwargs["search_depth"] = search_depth
        if include_domains is not None:
            tool_kwargs["include_domains"] = include_domains
        if exclude_domains is not None:
            tool_kwargs["exclude_domains"] = exclude_domains
    else:
        tool_kwargs["search_depth"] = search_depth
        if include_domains is not None:
            tool_kwargs["include_domains"] = include_domains
        else:
            tool_kwargs["include_domains"] = []
        if exclude_domains is not None:
            tool_kwargs["exclude_domains"] = exclude_domains
        else:
            tool_kwargs["exclude_domains"] = []

    try:
        tool_instance = TavilySearch(**tool_kwargs)
        return tool_instance
    except Exception as e:
        logger.error(f"❌ 创建 Tavily 搜索工具失败: {e}")
        raise


@tool
def web_search(query: str) -> str:
    """
    搜索互联网获取最新信息

    这个工具用于回答需要最新信息的问题，例如新闻、事件、技术更新等。
    对于事实性问题，应优先使用搜索工具获取准确信息。

    **注意：不要在搜索前调用时间工具，天气工具也不需要时间工具！**

    Args:
        query: 搜索查询关键词

    Returns:
        搜索结果列表，包含标题、链接和摘要

    Example:
        >>> web_search("LangChain 1.0 新特性")
        [{'title': 'LangChain 1.0 Release Notes', 'url': '...', 'content': '...'}]
    """
    try:
        tool = create_tavily_search_tool()
        results = tool.invoke(query)

        if not results:
            return "未找到相关结果"

        if isinstance(results, str):
            return results

        if not isinstance(results, list):
            return str(results)

        formatted_results = []
        for i, result in enumerate(results, 1):
            if isinstance(result, str):
                formatted_results.append(f"{i}. {result}")
                continue
            if not isinstance(result, dict):
                formatted_results.append(f"{i}. {str(result)}")
                continue
            title = result.get("title", "无标题")
            url = result.get("url", "")
            content = result.get("content", "")[:300]

            formatted_results.append(
                f"{i}. {title}\n   URL: {url}\n   摘要: {content}..."
            )

        output = "搜索结果：\n\n" + "\n\n".join(formatted_results)
        logger.info(f"🔍 搜索完成，返回 {len(results)} 条结果")
        return output

    except Exception as e:
        error_msg = f"搜索失败: {str(e)}"
        logger.error(error_msg)
        return error_msg


def get_web_search_tools():
    return [web_search]