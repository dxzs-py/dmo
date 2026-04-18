"""
Core Tools 模块 - 提供各种工具函数供 Agent 使用
"""

from .time_tools import get_current_time, get_current_date, get_time_tools
from .calculator import calculator, get_calculator_tools
from .web_search import web_search, create_tavily_search_tool, get_web_search_tools
from .weather import weather_query, get_weather_tools, WEATHER_TOOLS
from .filesystem import (
    fs_write_file,
    fs_read_file,
    fs_list_files,
    fs_search_files,
    FILESYSTEM_TOOLS,
    get_filesystem_tools,
    ResearchFileSystem,
)
from .translation_tool import translate_text, detect_language, get_translation_tools, TRANSLATION_TOOLS
from .file_reader_tool import file_reader, attachment_reader, get_file_reader_tools, FILE_READER_TOOLS
from .duckduckgo_search import duckduckgo_search, get_duckduckgo_tools, DUCKDUCKGO_TOOLS, has_duckduckgo_available
from .web_fetch import web_fetch, get_web_fetch_tools
from .todo_tools import todo_write, todo_read, get_todo_tools
from .agent_tools import agent_create, agent_run, agent_list, get_agent_tools

from typing import List, Optional
from langchain_core.tools import BaseTool

import logging

logger = logging.getLogger(__name__)

__all__ = [
    "get_current_time",
    "get_current_date",
    "get_time_tools",
    "calculator",
    "get_calculator_tools",
    "web_search",
    "web_search_simple",
    "create_tavily_search_tool",
    "get_web_search_tools",
    "weather_query",
    "get_daily_weather",
    "get_weather_tools",
    "WEATHER_TOOLS",
    "fs_write_file",
    "fs_read_file",
    "fs_list_files",
    "fs_search_files",
    "FILESYSTEM_TOOLS",
    "get_filesystem_tools",
    "ResearchFileSystem",
    "translate_text",
    "detect_language",
    "get_translation_tools",
    "TRANSLATION_TOOLS",
    "file_reader",
    "attachment_reader",
    "get_file_reader_tools",
    "FILE_READER_TOOLS",
    "duckduckgo_search",
    "get_duckduckgo_tools",
    "DUCKDUCKGO_TOOLS",
    "has_duckduckgo_available",
    "web_fetch",
    "get_web_fetch_tools",
    "todo_write",
    "todo_read",
    "get_todo_tools",
    "agent_create",
    "agent_run",
    "agent_list",
    "get_agent_tools",
    "get_all_basic_tools",
    "get_all_advanced_tools",
    "get_all_tools",
    "BASIC_TOOLS",
    "ADVANCED_TOOLS",
    "ALL_TOOLS",
    "get_tools_for_request",
]


def get_all_basic_tools() -> List[BaseTool]:
    tools = []
    tools.extend(get_time_tools())
    tools.extend(get_calculator_tools())
    tools.extend(get_translation_tools())
    return tools


def get_all_advanced_tools() -> List[BaseTool]:
    tools = get_all_basic_tools()
    tools.extend(get_web_search_tools())
    tools.extend(get_duckduckgo_tools())
    tools.extend(get_weather_tools())
    tools.extend(get_filesystem_tools())
    tools.extend(get_file_reader_tools())
    tools.extend(get_web_fetch_tools())
    tools.extend(get_todo_tools())
    tools.extend(get_agent_tools())
    return tools


def get_all_tools() -> List[BaseTool]:
    return get_all_advanced_tools()


BASIC_TOOLS = get_all_basic_tools()
ADVANCED_TOOLS = get_all_advanced_tools()
ALL_TOOLS = get_all_tools()


web_search_simple = web_search
get_daily_weather = weather_query


def get_tools_for_request(
    use_tools: bool,
    use_advanced_tools: bool,
    use_web_search: bool = False,
    attachment_ids: Optional[List[int]] = None,
) -> List:
    """
    根据请求参数获取工具列表

    Args:
        use_tools: 是否启用工具
        use_advanced_tools: 是否启用高级工具
        use_web_search: 是否启用联网搜索
        attachment_ids: 附件 ID 列表，有附件时自动添加文件读取工具
    """
    from Django_xm.apps.core.config import settings

    if not use_tools:
        return []

    tools: List = list(BASIC_TOOLS)

    if getattr(settings, 'amap_key', None):
        for tool_func in [get_daily_weather]:
            if tool_func not in tools:
                tools.append(tool_func)

    if use_web_search or use_advanced_tools:
        has_tavily = bool(getattr(settings, 'tavily_api_key', None))
        has_ddg = has_duckduckgo_available()

        if has_tavily:
            for tool_func in [web_search, web_search_simple]:
                if tool_func not in tools:
                    tools.append(tool_func)

        if has_ddg:
            if duckduckgo_search not in tools:
                tools.append(duckduckgo_search)

        if not has_tavily and not has_ddg:
            logger.warning("联网搜索不可用：未配置 Tavily API Key 且未安装 duckduckgo-search")

    if use_advanced_tools:
        tools.extend(get_filesystem_tools())
        tools.extend(get_web_fetch_tools())
        tools.extend(get_todo_tools())

    if use_advanced_tools or use_web_search:
        tools.extend(get_agent_tools())

    if attachment_ids:
        for t in get_file_reader_tools():
            if t not in tools:
                tools.append(t)

    return tools
