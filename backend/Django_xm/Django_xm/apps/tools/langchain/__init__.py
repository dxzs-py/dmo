"""LangChain 框架工具集

统一存放所有基于 LangChain BaseTool 的内置工具。
按功能域拆分为独立模块，通过本文件聚合导出。
"""

# 时间工具
from .time import get_current_time, get_current_date, get_time_tools

# 计算器工具
from .calc import calculator, get_calculator_tools

# 网络搜索工具
from .web_search import (
    web_search, create_tavily_search_tool,
    get_web_search_tools, get_tavily_api_key, get_tavily_max_results,
)
from .web_duckduckgo import (
    duckduckgo_search, get_duckduckgo_tools, DUCKDUCKGO_TOOLS,
    has_duckduckgo_available,
)
from .web_fetch import web_fetch, get_web_fetch_tools

# 天气工具
from .weather import weather_query, get_weather_tools, WEATHER_TOOLS
get_daily_weather = weather_query  # 别名，与 tools/__init__.py 保持一致

# 文件系统工具
from .filesystem import (
    fs_write_file, fs_read_file, fs_list_files, fs_search_files,
    FILESYSTEM_TOOLS, get_filesystem_tools, ResearchFileSystem,
)

# 文件读取工具
from .file_reader import (
    file_reader, attachment_reader, get_file_reader_tools, FILE_READER_TOOLS,
    read_file_content, read_file_as_documents,
    read_attachment_as_base64, read_attachment_as_documents,
    get_attachment_info, read_multiple_attachments,
)

# 翻译工具
from .translation import translate_text, detect_language, get_translation_tools, TRANSLATION_TOOLS

# 待办工具
from .todo import todo_write, todo_read, get_todo_tools

# 子代理工具
from .agent import (
    agent_create, agent_run, agent_list, agent_cleanup,
    get_agent_tools, AGENT_TYPES,
)

# Shell 执行工具
from .shell import shell_exec, get_shell_exec_tools

__all__ = [
    # 时间
    "get_current_time", "get_current_date", "get_time_tools",
    # 计算器
    "calculator", "get_calculator_tools",
    # 搜索
    "web_search", "create_tavily_search_tool", "get_web_search_tools",
    "duckduckgo_search", "get_duckduckgo_tools", "DUCKDUCKGO_TOOLS", "has_duckduckgo_available",
    "web_fetch", "get_web_fetch_tools",
    # 天气
    "weather_query", "get_daily_weather", "get_weather_tools", "WEATHER_TOOLS",
    # 文件系统
    "fs_write_file", "fs_read_file", "fs_list_files", "fs_search_files",
    "FILESYSTEM_TOOLS", "get_filesystem_tools", "ResearchFileSystem",
    # 文件读取
    "file_reader", "attachment_reader", "get_file_reader_tools", "FILE_READER_TOOLS",
    # 翻译
    "translate_text", "detect_language", "get_translation_tools", "TRANSLATION_TOOLS",
    # 待办
    "todo_write", "todo_read", "get_todo_tools",
    # 子代理
  "agent_create", "agent_run", "agent_list", "agent_cleanup",
  "get_agent_tools", "AGENT_TYPES",
  # Shell 执行
  "shell_exec", "get_shell_exec_tools",
]
