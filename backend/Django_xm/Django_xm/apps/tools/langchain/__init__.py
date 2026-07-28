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

get_daily_weather = weather_query  # 别名，与 tools/__init__.py 保持一致

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
  # Shell 执行
  "shell_exec", "get_shell_exec_tools",
]
