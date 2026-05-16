from .time import get_current_time, get_current_date, get_time_tools
from .calc import calculator, get_calculator_tools
from .web.search import web_search, create_tavily_search_tool, get_web_search_tools
from .web.duckduckgo import duckduckgo_search, get_duckduckgo_tools, DUCKDUCKGO_TOOLS, has_duckduckgo_available
from .web.fetch import web_fetch, get_web_fetch_tools
from .weather import weather_query, get_weather_tools, WEATHER_TOOLS
from .file.filesystem import (
    fs_write_file, fs_read_file, fs_list_files, fs_search_files,
    FILESYSTEM_TOOLS, get_filesystem_tools, ResearchFileSystem,
)
from .file.reader import file_reader, attachment_reader, get_file_reader_tools, FILE_READER_TOOLS
from .translation import translate_text, detect_language, get_translation_tools, TRANSLATION_TOOLS
from .todo import todo_write, todo_read, get_todo_tools
from .agent import agent_create, agent_run, agent_list, get_agent_tools

from typing import List, Optional, Dict
from langchain_core.tools import BaseTool
import logging

logger = logging.getLogger(__name__)

web_search_simple = web_search
get_daily_weather = weather_query


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


get_basic_tools = get_all_basic_tools
BASIC_TOOLS = get_all_basic_tools()
ADVANCED_TOOLS = get_all_advanced_tools()
ALL_TOOLS = get_all_tools()


def get_tools_for_request(
    use_tools: bool,
    use_advanced_tools: bool,
    use_web_search: bool = False,
    use_mcp: bool = False,
    attachment_ids: Optional[List[int]] = None,
) -> List:
    from Django_xm.apps.ai_engine.config import settings

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

    if use_mcp:
        mcp_tools = _load_mcp_tools_sync()
        if mcp_tools:
            tools.extend(mcp_tools)
            logger.info(f"MCP 工具已加载 ({len(mcp_tools)} 个)")
        else:
            logger.debug("未获取到 MCP 工具（可能未配置 MCP Server 或连接失败）")

    return tools


async def _load_mcp_tools_async(selected_servers: Optional[List[str]] = None) -> List[BaseTool]:
    try:
        from Django_xm.apps.tools.mcp import is_mcp_available, get_mcp_tools, _get_mcp_servers_config
        if not is_mcp_available():
            logger.warning("MCP 不可用: langchain-mcp-adapters 未安装")
            return []
        servers = _get_mcp_servers_config()
        logger.info(f"MCP 配置加载: {len(servers)} 个服务器")
        if not servers:
            logger.warning("MCP 配置为空: 未配置任何 MCP Server")
            return []
        if selected_servers:
            servers = [s for s in servers if s.get("name") in selected_servers]
            logger.info(f"MCP 过滤: 选中 {len(servers)} 个服务器: {selected_servers}")
        all_mcp_tools: List[BaseTool] = []
        for srv in servers:
            transport = srv.get("transport", "sse")
            try:
                logger.info(f"正在加载 MCP Server: {srv.get('name')} (transport={transport})")
                if transport == "stdio":
                    tools = await get_mcp_tools(
                        server_name=srv.get("name"),
                        transport="stdio",
                        command=srv.get("command"),
                        args=srv.get("args"),
                        env=srv.get("env"),
                    )
                else:
                    url = srv.get("url")
                    if not url:
                        logger.warning(f"MCP Server '{srv.get('name')}' 缺少 url")
                        continue
                    tools = await get_mcp_tools(
                        server_url=url,
                        transport=transport,
                        headers=srv.get("headers"),
                        auth_token=srv.get("auth_token"),
                    )
                logger.info(f"MCP Server '{srv.get('name')}' 返回 {len(tools)} 个工具")
                all_mcp_tools.extend(tools)
            except Exception as e:
                logger.warning(f"MCP Server ({srv.get('name', 'unknown')}) 工具获取失败: {e}", exc_info=True)
        from Django_xm.apps.tools.mcp import _get_local_mcp_tools
        local_tools = _get_local_mcp_tools()
        if local_tools:
            all_mcp_tools.extend(local_tools)
            logger.info(f"本地 MCP 工具已加载 ({len(local_tools)} 个)")
        logger.info(f"MCP 工具加载完成: 共 {len(all_mcp_tools)} 个")
        return all_mcp_tools
    except ImportError as e:
        logger.warning(f"MCP 模块导入失败: {e}")
        return []
    except Exception as e:
        logger.warning(f"MCP 工具加载异常: {e}", exc_info=True)
        return []


def get_all_available_tool_info() -> List[Dict]:
    result = []
    seen = set()
    for tool in get_all_tools():
        if tool.name in seen:
            continue
        seen.add(tool.name)
        category = "basic"
        if tool.name in [t.name for t in get_web_search_tools() + get_duckduckgo_tools()]:
            category = "web_search"
        elif tool.name in [t.name for t in get_filesystem_tools() + get_file_reader_tools()]:
            category = "file"
        elif tool.name in [t.name for t in get_weather_tools()]:
            category = "weather"
        elif tool.name in [t.name for t in get_translation_tools()]:
            category = "translation"
        elif tool.name in [t.name for t in get_agent_tools()]:
            category = "agent"
        elif tool.name in [t.name for t in get_todo_tools()]:
            category = "todo"
        elif tool.name in [t.name for t in get_web_fetch_tools()]:
            category = "web_fetch"
        result.append({
            "name": tool.name,
            "description": tool.description or "",
            "category": category,
        })
    return result


async def get_tools_for_request_async(
    use_tools: bool,
    use_advanced_tools: bool,
    use_web_search: bool = False,
    use_mcp: bool = False,
    attachment_ids: Optional[List[int]] = None,
    selected_mcp_servers: Optional[List[str]] = None,
    selected_tools: Optional[List[str]] = None,
) -> List:
    if selected_tools:
        all_available = get_all_tools()
        tools = [t for t in all_available if t.name in selected_tools]
        logger.info(f"使用选中的工具 ({len(tools)} 个): {selected_tools}")
    else:
        tools = get_tools_for_request(
            use_tools=use_tools,
            use_advanced_tools=use_advanced_tools,
            use_web_search=use_web_search,
            use_mcp=False,
            attachment_ids=attachment_ids,
        )
    if use_mcp:
        mcp_tools = await _load_mcp_tools_async(selected_servers=selected_mcp_servers)
        if mcp_tools:
            tools.extend(mcp_tools)
            logger.info(f"MCP 工具已加载 ({len(mcp_tools)} 个)")
        else:
            logger.debug("未获取到 MCP 工具（可能未配置 MCP Server 或连接失败）")
    return tools


__all__ = [
    "get_current_time", "get_current_date", "get_time_tools",
    "calculator", "get_calculator_tools",
    "web_search", "web_search_simple", "create_tavily_search_tool", "get_web_search_tools",
    "duckduckgo_search", "get_duckduckgo_tools", "DUCKDUCKGO_TOOLS", "has_duckduckgo_available",
    "web_fetch", "get_web_fetch_tools",
    "weather_query", "get_daily_weather", "get_weather_tools", "WEATHER_TOOLS",
    "fs_write_file", "fs_read_file", "fs_list_files", "fs_search_files",
    "FILESYSTEM_TOOLS", "get_filesystem_tools", "ResearchFileSystem",
    "file_reader", "attachment_reader", "get_file_reader_tools", "FILE_READER_TOOLS",
    "translate_text", "detect_language", "get_translation_tools", "TRANSLATION_TOOLS",
    "todo_write", "todo_read", "get_todo_tools",
    "agent_create", "agent_run", "agent_list", "get_agent_tools",
    "BASIC_TOOLS", "ADVANCED_TOOLS", "ALL_TOOLS",
    "get_all_basic_tools", "get_all_advanced_tools", "get_all_tools",
    "get_tools_for_request", "get_tools_for_request_async",
    "get_all_available_tool_info",
]
