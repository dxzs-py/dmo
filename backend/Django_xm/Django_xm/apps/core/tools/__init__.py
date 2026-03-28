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

from typing import List
from langchain_core.tools import BaseTool

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
    "get_all_basic_tools",
    "get_all_advanced_tools",
    "get_all_tools",
    "BASIC_TOOLS",
    "ADVANCED_TOOLS",
    "ALL_TOOLS",
    "get_tools_for_request",
]


def get_all_basic_tools() -> List[BaseTool]:
    """获取所有基础工具"""
    tools = []
    tools.extend(get_time_tools())
    tools.extend(get_calculator_tools())
    return tools


def get_all_advanced_tools() -> List[BaseTool]:
    """获取所有高级工具（包括搜索和文件系统）"""
    tools = get_all_basic_tools()
    tools.extend(get_web_search_tools())
    tools.extend(get_filesystem_tools())
    return tools


def get_all_tools() -> List[BaseTool]:
    """获取所有工具"""
    return get_all_advanced_tools()


BASIC_TOOLS = get_all_basic_tools()
ADVANCED_TOOLS = get_all_advanced_tools()
ALL_TOOLS = get_all_tools()


web_search_simple = web_search
get_daily_weather = weather_query


def get_tools_for_request(use_tools: bool, use_advanced_tools: bool) -> List:
    """根据请求参数获取工具列表"""
    from Django_xm.apps.core.config import settings

    if not use_tools:
        return []

    tools: List = list(BASIC_TOOLS)

    if getattr(settings, 'amap_key', None):
        for tool_func in [get_daily_weather]:
            if tool_func not in tools:
                tools.append(tool_func)

    if getattr(settings, 'tavily_api_key', None):
        for tool_func in [web_search, web_search_simple]:
            if tool_func not in tools:
                tools.append(tool_func)

    return tools