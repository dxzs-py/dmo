"""LangChain 框架工具集

统一存放所有基于 LangChain BaseTool 的内置工具。
按功能域拆分为独立模块，通过本文件聚合导出。
"""

# 时间工具
# 计算器工具
from .calc import calculator, get_calculator_tools
from .time import get_current_date, get_current_time, get_time_tools

# 天气工具
from .weather import WEATHER_TOOLS, get_weather_tools, weather_query
from .web_duckduckgo import (
    DUCKDUCKGO_TOOLS,
    duckduckgo_search,
    get_duckduckgo_tools,
    has_duckduckgo_available,
)
from .web_fetch import get_web_fetch_tools, web_fetch

# 网络搜索工具
from .web_search import (
    create_tavily_search_tool,
    get_tavily_api_key,
    get_tavily_max_results,
    get_web_search_tools,
    web_search,
)

# 文件系统工具
# 文件读取工具
from .file_reader import (
    FILE_READER_TOOLS,
    attachment_reader,
    file_reader,
    get_attachment_info,
    get_file_reader_tools,
    read_attachment_as_base64,
    read_attachment_as_documents,
    read_file_as_documents,
    read_file_content,
    read_multiple_attachments,
)
from .filesystem import (
    FILESYSTEM_TOOLS,
    ResearchFileSystem,
    fs_list_files,
    fs_read_file,
    fs_search_files,
    fs_write_file,
    get_filesystem_tools,
)

# Shell 执行工具
from .shell import get_shell_exec_tools, shell_exec

# 待办工具
from .todo import get_todo_tools, todo_read, todo_write

# 翻译工具
from .translation import TRANSLATION_TOOLS, detect_language, get_translation_tools, translate_text

__all__ = [
    "DUCKDUCKGO_TOOLS",
    "FILESYSTEM_TOOLS",
    "FILE_READER_TOOLS",
    "TRANSLATION_TOOLS",
    "WEATHER_TOOLS",
    "ResearchFileSystem",
    "attachment_reader",
    # 计算器
    "calculator",
    "create_tavily_search_tool",
    "detect_language",
    "duckduckgo_search",
    # 文件读取
    "file_reader",
    "fs_list_files",
    "fs_read_file",
    "fs_search_files",
    # 文件系统
    "fs_write_file",
    "get_calculator_tools",
    "get_current_date",
    # 时间
    "get_current_time",
    "get_duckduckgo_tools",
    "get_file_reader_tools",
    "get_filesystem_tools",
    "get_shell_exec_tools",
    "get_time_tools",
    "get_todo_tools",
    "get_translation_tools",
    "get_weather_tools",
    "get_web_fetch_tools",
    "get_web_search_tools",
    "has_duckduckgo_available",
    # Shell 执行
    "shell_exec",
    "todo_read",
    # 待办
    "todo_write",
    # 翻译
    "translate_text",
    # 天气
    "weather_query",
    "web_fetch",
    # 搜索
    "web_search",
]
