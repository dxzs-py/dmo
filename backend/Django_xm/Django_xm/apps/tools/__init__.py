from .langchain.time import get_current_time, get_current_date, get_time_tools
from .langchain.calc import calculator, get_calculator_tools
from .langchain.web_search import web_search, create_tavily_search_tool, get_web_search_tools
from .langchain.web_duckduckgo import duckduckgo_search, get_duckduckgo_tools, DUCKDUCKGO_TOOLS, has_duckduckgo_available
from .langchain.web_fetch import web_fetch, get_web_fetch_tools
from .langchain.weather import weather_query, get_weather_tools, WEATHER_TOOLS
from .langchain.filesystem import (
    fs_write_file, fs_read_file, fs_list_files, fs_search_files,
    FILESYSTEM_TOOLS, get_filesystem_tools, ResearchFileSystem,
)
from .langchain.file_reader import file_reader, attachment_reader, get_file_reader_tools, FILE_READER_TOOLS
from .langchain.translation import translate_text, detect_language, get_translation_tools, TRANSLATION_TOOLS
from .langchain.todo import todo_write, todo_read, get_todo_tools
from .langchain.agent import agent_create, agent_run, agent_list, agent_cleanup, get_agent_tools
from .langchain.shell import shell_exec, get_shell_exec_tools
from .errors import (
    ToolErrorCode, ToolError, ToolResult, ToolStatus, StandardToolResult,
    create_tool_result, create_tool_error, exception_to_tool_error, TOOL_VERSION,
)

from typing import Any, List, Optional, Dict
from langchain_core.tools import BaseTool
import logging

logger = logging.getLogger(__name__)

def _deduplicate_tools(tools: List[BaseTool]) -> List[BaseTool]:
    seen = set()
    result = []
    for tool in tools:
        if tool.name not in seen:
            seen.add(tool.name)
            result.append(tool)
    return result

get_daily_weather = weather_query


TOOL_TIER_CORE = "core"
TOOL_TIER_STANDARD = "standard"
TOOL_TIER_EXTENDED = "extended"

def get_core_tools() -> List[BaseTool]:
    return [t for t in get_all_tools() if (t.metadata or {}).get("tier") == "core"]

def get_standard_tools() -> List[BaseTool]:
    return [t for t in get_all_tools() if (t.metadata or {}).get("tier") in ("core", "standard")]

def _get_extended_tools() -> List[BaseTool]:
    return get_all_tools()


def get_all_basic_tools() -> List[BaseTool]:
    return get_core_tools()


def _get_advanced_tools() -> List[BaseTool]:
    return _get_extended_tools()


def _get_web_search_tools() -> List[BaseTool]:
    """联网搜索工具（仅由 use_web_search 控制）"""
    from Django_xm.apps.ai_engine.config import settings
    tools = []
    has_tavily = bool(getattr(settings, 'tavily_api_key', None))
    has_ddg = has_duckduckgo_available()

    if has_tavily:
        if web_search not in tools:
            tools.append(web_search)

    if has_ddg:
        if duckduckgo_search not in tools:
            tools.append(duckduckgo_search)

    if not has_tavily and not has_ddg:
        logger.warning("联网搜索不可用：未配置 Tavily API Key 且未安装 duckduckgo-search")

    return tools


def _get_attachment_tools(attachment_ids: Optional[List[int]] = None) -> List[BaseTool]:
    """附件工具"""
    if not attachment_ids:
        return []
    tools = []
    for t in get_file_reader_tools():
        if t not in tools:
            tools.append(t)
    return tools


def _filter_tools_by_names(tools: List[BaseTool], selected_tools: List[str]) -> List[BaseTool]:
    """按名称过滤工具"""
    selected_set = set(selected_tools)
    return [t for t in tools if t.name in selected_set]


def get_all_advanced_tools() -> List[BaseTool]:
    tools = []
    tools.extend(get_time_tools())
    tools.extend(get_calculator_tools())
    tools.extend(_get_web_search_tools())
    tools.extend(get_weather_tools())
    tools.extend(get_filesystem_tools())
    tools.extend(get_file_reader_tools())
    tools.extend(get_web_fetch_tools())
    tools.extend(get_todo_tools())
    tools.extend(get_agent_tools())
    tools.extend(get_translation_tools())
    tools.extend(get_shell_exec_tools())
    return tools


def get_all_tools() -> List[BaseTool]:
    return get_all_advanced_tools()


get_basic_tools = get_all_basic_tools

# 懒加载常量：避免模块级实例化所有工具导致的循环依赖和启动开销
_TOOLS_CACHE: dict = {}

def __getattr__(name):
    if name in ('BASIC_TOOLS', 'ADVANCED_TOOLS', 'ALL_TOOLS'):
        if name not in _TOOLS_CACHE:
            if name == 'BASIC_TOOLS':
                _TOOLS_CACHE[name] = get_all_basic_tools()
            elif name == 'ADVANCED_TOOLS':
                _TOOLS_CACHE[name] = get_all_advanced_tools()
            elif name == 'ALL_TOOLS':
                _TOOLS_CACHE[name] = get_all_tools()
        return _TOOLS_CACHE[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def _load_mcp_tools_async(selected_servers: Optional[List[str]] = None, user_id: Optional[int] = None, selected_tools: Optional[List[str]] = None) -> List[BaseTool]:
    try:
        from Django_xm.apps.tools.mcp import is_mcp_available, get_mcp_tools, _get_mcp_servers_config
        if not is_mcp_available():
            logger.warning("MCP 不可用: langchain-mcp-adapters 未安装")
            return []
        # 合并系统级和用户级 MCP Server
        servers = _get_mcp_servers_config()
        if user_id:
            try:
                from asgiref.sync import sync_to_async
                from Django_xm.apps.tools.models import McpServerConfig

                @sync_to_async
                def _get_user_servers():
                    return list(McpServerConfig.objects.filter(user_id=user_id, status='active'))

                user_servers = await _get_user_servers()
                seen = {s.get("name") for s in servers}
                for srv in user_servers:
                    if srv.name not in seen:
                        servers.append(srv.to_config_dict())
                        seen.add(srv.name)
            except Exception as e:
                logger.warning(f"加载用户 MCP Server 配置失败: {e}")

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
                        server_name=srv.get("name"),
                        transport=transport,
                        headers=srv.get("headers"),
                        auth_token=srv.get("auth_token"),
                    )
                logger.info(f"MCP Server '{srv.get('name')}' 返回 {len(tools)} 个工具")
                all_mcp_tools.extend(tools)
            except Exception as e:
                logger.warning(f"MCP Server ({srv.get('name', 'unknown')}) 工具获取失败: {e}", exc_info=True)
        for tool in all_mcp_tools:
            if not hasattr(tool, "metadata") or tool.metadata is None:
                tool.metadata = {}
            tool.metadata["is_mcp_tool"] = True
        logger.info(f"MCP 工具加载完成: 共 {len(all_mcp_tools)} 个")
        return all_mcp_tools
    except ImportError as e:
        logger.warning(f"MCP 模块导入失败: {e}")
        return []
    except Exception as e:
        logger.warning(f"MCP 工具加载异常: {e}", exc_info=True)
        return []


def get_all_available_tool_info() -> List[Dict[str, Any]]:
    tools = get_all_tools()
    result = []
    seen = set()
    for tool in tools:
        if tool.name in seen:
            continue
        seen.add(tool.name)
        meta = getattr(tool, 'metadata', None) or {}
        result.append({
            "name": tool.name,
            "description": tool.description or "",
            "tier": meta.get("tier", "extended"),
            "visibility": meta.get("visibility", "selectable"),
            "category": meta.get("category", "other"),
            "source": "system",
        })
    return result


async def get_tools_for_request_async(
    use_tools: bool = True,
    use_web_search: bool = False,
    use_mcp: bool = False,
    selected_mcp_servers: Optional[List[str]] = None,
    selected_tools: Optional[List[str]] = None,
    attachment_ids: Optional[List[int]] = None,
    user_id: Optional[int] = None,
    tool_tier: str = TOOL_TIER_STANDARD,
) -> List:
    if not use_tools:
        return []

    mcp_tools: List[BaseTool] = []
    should_load_mcp = use_mcp

    if selected_tools:
        selected_set = set(selected_tools)
        builtin_names = {t.name for t in get_all_tools()}
        # skill 工具名以 skill_ 开头：包含预置 + 用户自定义 SkillConfig
        skill_names = {f"skill_{s.name}" for s in SkillRegistryService.get_presets()}
        # 自定义工具名从数据库查询（不过滤 status，否则禁用状态的工具名会被误判为 MCP）
        custom_names = set()
        user_skill_config_names = set()
        if user_id:
            try:
                from asgiref.sync import sync_to_async
                from Django_xm.apps.tools.models import CustomTool, SkillConfig

                @sync_to_async
                def _get_custom_and_skill_names():
                    cn = set(CustomTool.objects.filter(user_id=user_id).values_list('name', flat=True))
                    sn = set(SkillConfig.objects.filter(user_id=user_id).values_list('name', flat=True))
                    return cn, sn

                custom_names, user_skill_config_names = await _get_custom_and_skill_names()
            except Exception:
                pass

        # SkillPackage 名称
        skill_package_names = set()
        if user_id:
            try:
                from asgiref.sync import sync_to_async
                from Django_xm.apps.tools.models import SkillPackage

                @sync_to_async
                def _get_skill_package_names():
                    return set(SkillPackage.objects.filter(user_id=user_id).values_list('name', flat=True))

                pkg_names = await _get_skill_package_names()
                skill_package_names = {f"skill_{n}" for n in pkg_names}
            except Exception:
                pass

        # 用户自定义 SkillConfig 名称也加 skill_ 前缀
        user_skill_names = {f"skill_{n}" for n in user_skill_config_names}
        known_names = builtin_names | skill_names | custom_names | skill_package_names | user_skill_names
        mcp_selected = selected_set - known_names
        builtin_selected = selected_set & builtin_names

        tools = [t for t in get_all_tools() if t.name in builtin_selected]

        # 自定义工具在 selected_tools 中
        if user_id and custom_names:
            selected_custom = selected_set & custom_names
            if selected_custom:
                from asgiref.sync import sync_to_async
                custom_tools = await sync_to_async(_load_custom_tools_for_user, thread_sensitive=True)(user_id, selected_names=list(selected_custom))
                if custom_tools:
                    tools.extend(custom_tools)

        if mcp_selected:
            should_load_mcp = True
            all_mcp = await _load_mcp_tools_async(selected_servers=selected_mcp_servers, user_id=user_id, selected_tools=selected_tools)
            mcp_tools = [t for t in all_mcp if t.name in mcp_selected]
            if mcp_tools:
                logger.info(f"选中 MCP 工具 ({len(mcp_tools)} 个): {mcp_selected}")
            else:
                server_matched_tools = [
                    t for t in all_mcp
                    if (t.metadata or {}).get('mcp_server_name', '') in mcp_selected
                ]
                if server_matched_tools:
                    mcp_tools = server_matched_tools
                    logger.info(f"按 MCP Server 名称匹配工具 ({len(mcp_tools)} 个): {mcp_selected}")
                elif selected_mcp_servers:
                    server_name_set = set(selected_mcp_servers)
                    if mcp_selected & server_name_set:
                        mcp_tools = all_mcp
                        logger.info(f"MCP Server 名称匹配，加载工具 ({len(mcp_tools)} 个): {selected_mcp_servers}")
                    else:
                        logger.warning(f"选中的工具名未匹配到任何已知工具或 MCP 工具: {mcp_selected}")
                        should_load_mcp = False
                else:
                    logger.warning(f"选中的工具名未匹配到任何已知工具或 MCP 工具: {mcp_selected}")
                    should_load_mcp = False
    else:
        if tool_tier == TOOL_TIER_CORE:
            tools = get_core_tools()
        elif tool_tier == TOOL_TIER_STANDARD:
            tools = get_standard_tools()
        else:
            tools = list(get_all_tools())
        if use_web_search:
            tools.extend(_get_web_search_tools())

    if should_load_mcp and not mcp_tools:
        mcp_tools = await _load_mcp_tools_async(selected_servers=selected_mcp_servers, user_id=user_id, selected_tools=selected_tools)
        if mcp_tools:
            logger.info(f"MCP 工具已加载 ({len(mcp_tools)} 个)")
        else:
            logger.debug("未获取到 MCP 工具（可能未配置 MCP Server 或连接失败）")

    if mcp_tools:
        tools.extend(mcp_tools)

    # 仅在用户精确选择了工具时加载技能工具
    # selected_tools=None 表示用户未在 ToolSelector 中选择具体工具，
    # 此时只加载 builtin + web_search + MCP，不自动加载全部 skill/custom
    if selected_tools:
        # 通过 SkillProvider 加载选中的技能工具
        skill_tools = await SkillProvider.get_skill_tools(
            user_id=user_id,
            available_tools=tools,
            selected_tools=selected_tools,
        )
        if skill_tools:
            tools.extend(skill_tools)
            logger.info(f"技能工具已加载 ({len(skill_tools)} 个)")

    return _deduplicate_tools(tools)


from .skills import (
    SkillStep, SkillSpec, SkillRegistryService, PRESET_SKILLS,
    SkillBaseTool, create_skill_base_tools,
    SkillAdapter, SkillProvider,
    SkillLoader,
)


def _load_custom_tools_for_user(user_id: int, selected_names: Optional[List[str]] = None) -> List[BaseTool]:
    """从数据库加载用户自定义工具，动态实例化为 BaseTool"""
    try:
        from Django_xm.apps.tools.models import CustomTool
        qs = CustomTool.objects.filter(user_id=user_id, status='active')
        if selected_names:
            qs = qs.filter(name__in=selected_names)
        tools = []
        for tool_obj in qs:
            try:
                tool_instance = _instantiate_custom_tool(tool_obj)
                if tool_instance:
                    tools.append(tool_instance)
            except Exception as e:
                logger.warning(f"自定义工具 '{tool_obj.name}' 加载失败: {e}")
        return tools
    except Exception as e:
        logger.warning(f"加载自定义工具失败: {e}")
        return []


_ALLOWED_IMPORTS = {
    'json', 'math', 're', 'textwrap',
    'datetime', 'time', 'calendar', 'collections', 'itertools',
    'functools', 'copy', 'hashlib', 'base64',
    'typing', 'pathlib', 'urllib.parse', 'uuid', 'decimal',
    'string', 'random', 'statistics',
    'fractions', 'dataclasses', 'enum', 'abc', 'html',
    'langchain_core.tools',
}
_ALLOWED_IMPORT_ROOTS = {m.split('.')[0] for m in _ALLOWED_IMPORTS}


def _validate_tool_code_safety(code: str) -> tuple[bool, str]:
    """AST 静态检查用户工具代码安全性"""
    import ast
    FORBIDDEN_NAMES = {
        '__import__', '__builtins__', 'exec', 'eval',
        'compile', 'input', '__class__', '__subclasses__',
        '__bases__', '__mro__',
        'getattr', 'hasattr', 'setattr', 'delattr',
    }
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"语法错误: {e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split('.')[0]
                if root_mod not in _ALLOWED_IMPORT_ROOTS:
                    return False, f"禁止导入模块 '{alias.name}': 第{node.lineno}行"
        if isinstance(node, ast.ImportFrom):
            if node.module:
                root_mod = node.module.split('.')[0]
                if root_mod not in _ALLOWED_IMPORT_ROOTS:
                    return False, f"禁止导入模块 '{node.module}': 第{node.lineno}行"
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return False, f"禁止使用 {node.id}: 第{node.lineno}行"
        if isinstance(node, ast.Attribute) and node.attr.startswith('__') and node.attr.endswith('__'):
            return False, f"禁止访问双下划线属性 {node.attr}: 第{node.lineno}行"
    return True, ""


def _instantiate_custom_tool(tool_obj) -> Optional[BaseTool]:
    """将数据库中的自定义工具代码动态实例化为 BaseTool

    安全策略：
    1. AST 静态检查禁止 import/危险函数/双下划线属性
    2. compile() 预检语法
    3. 受限命名空间执行
    4. 只提取 @tool 装饰器注册的 BaseTool 实例
    """
    from langchain_core.tools import BaseTool

    code = tool_obj.code
    is_safe, msg = _validate_tool_code_safety(code)
    if not is_safe:
        logger.error(f"自定义工具 '{tool_obj.name}' 安全检查失败: {msg}")
        return None

    safe_builtins = {
            k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
            for k in ('print', 'len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'tuple', 'set',
                       'range', 'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed',
                       'isinstance', 'issubclass', 'type', 'abs', 'min', 'max', 'sum', 'round',
                       'any', 'all', 'chr', 'ord', 'hex', 'oct', 'bin', 'pow', 'divmod',
                       'id', 'hash', 'repr', 'format', 'bytes', 'bytearray', 'frozenset', 'slice',
                       'object', 'super', 'property', 'staticmethod', 'classmethod', 'complex',
                       'ValueError', 'TypeError', 'KeyError', 'IndexError', 'AttributeError',
                       'NameError', 'RuntimeError', 'Exception', 'StopIteration',
                       'NotImplementedError', 'OverflowError', 'ZeroDivisionError',
                       'FileNotFoundError', 'PermissionError', 'OSError', 'IOError',
                       'AssertionError', 'ImportError', 'ModuleNotFoundError', 'LookupError',
                       'UnicodeError', 'ArithmeticError', 'BufferError',
                       'Warning', 'UserWarning', 'DeprecationWarning',
                       'None', 'True', 'False')
        }
    _real_import = __import__ if isinstance(__builtins__, dict) else __builtins__.__import__
    def _restricted_import(name, *args, **kwargs):
        root = name.split('.')[0]
        if root not in _ALLOWED_IMPORT_ROOTS:
            raise ImportError(f"禁止导入模块 '{name}'")
        return _real_import(name, *args, **kwargs)
    safe_builtins['__import__'] = _restricted_import

    safe_globals = {
        "__builtins__": safe_builtins,
        "tool": __import__('langchain_core.tools', fromlist=['tool']).tool,
        "BaseTool": BaseTool,
    }
    for mod_name in ('json', 'math', 're', 'textwrap',
                      'datetime', 'time', 'calendar', 'collections', 'itertools',
                      'functools', 'copy', 'hashlib', 'base64',
                      'typing', 'pathlib', 'urllib.parse', 'uuid', 'decimal',
                      'string', 'random', 'statistics',
                      'fractions', 'dataclasses', 'enum', 'abc', 'html'):
        try:
            safe_globals[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    namespace = {}
    try:
        exec(compile(code, f"<custom_tool:{tool_obj.name}>", "exec"), safe_globals, namespace)
    except Exception as e:
        logger.warning(f"自定义工具 '{tool_obj.name}' 执行失败: {e}")
        return None

    for obj in namespace.values():
        if isinstance(obj, BaseTool):
            return obj

    logger.warning(f"自定义工具 '{tool_obj.name}' 未找到 BaseTool 实例")
    return None

__all__ = [
    "get_current_time", "get_current_date", "get_time_tools",
    "calculator", "get_calculator_tools",
    "web_search", "create_tavily_search_tool", "get_web_search_tools",
    "duckduckgo_search", "get_duckduckgo_tools", "DUCKDUCKGO_TOOLS", "has_duckduckgo_available",
    "web_fetch", "get_web_fetch_tools",
    "weather_query", "get_daily_weather", "get_weather_tools", "WEATHER_TOOLS",
    "fs_write_file", "fs_read_file", "fs_list_files", "fs_search_files",
    "FILESYSTEM_TOOLS", "get_filesystem_tools", "ResearchFileSystem",
    "file_reader", "attachment_reader", "get_file_reader_tools", "FILE_READER_TOOLS",
    "translate_text", "detect_language", "get_translation_tools", "TRANSLATION_TOOLS",
    "todo_write", "todo_read", "get_todo_tools",
    "agent_create", "agent_run", "agent_list", "agent_cleanup", "get_agent_tools",
    "shell_exec", "get_shell_exec_tools",
    "ToolErrorCode", "ToolError", "ToolResult",
    "create_tool_result", "create_tool_error", "exception_to_tool_error",
    "BASIC_TOOLS", "ADVANCED_TOOLS", "ALL_TOOLS",
    "get_all_basic_tools", "get_all_advanced_tools", "get_all_tools",
    "get_tools_for_request_async",
    "get_all_available_tool_info",
    "TOOL_TIER_CORE", "TOOL_TIER_STANDARD", "TOOL_TIER_EXTENDED",
    "get_core_tools", "get_standard_tools",
    "SkillStep", "SkillSpec", "SkillRegistryService", "PRESET_SKILLS",
    "SkillBaseTool", "create_skill_base_tools",
    "SkillAdapter", "SkillProvider", "SkillLoader",
]
