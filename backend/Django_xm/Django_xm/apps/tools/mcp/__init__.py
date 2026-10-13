"""
MCP (Model Context Protocol) 工具集成模块

通过 langchain-mcp-adapters 将 MCP Server 的工具转换为 LangChain BaseTool，
使 Agent 可以调用外部 MCP 服务提供的工具。

支持传输协议: stdio / sse / http / websocket
支持远程服务器 + 本地自定义 MCP 工具
支持 LangGraph 和 LangChain Agent 两种框架集成

配置:
    系统级 Server 由本包 ``mcp/config.py`` 的 ``get_system_mcp_servers()`` 提供
    （Task 27.3 起不再走 Django settings）：
        - 生产环境：``MCP_SYSTEM_SERVERS_JSON`` 环境变量（JSON 数组）
        - 开发环境：内置 sequential-thinking（stdio）+ context7（http）
    早期曾支持在 settings.py 中配置，形如:
        MCP_SERVERS = [
            # stdio 传输（本地进程）
            {
                "name": "sequential-thinking",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
                "enabled": True,
            },
            # HTTP 传输（远程服务器）
            {
                "name": "context7",
                "url": "https://mcp.context7.com/mcp",
                "transport": "http",
                "enabled": True,
            },
            # SSE 传输
            {
                "name": "filesystem",
                "url": "http://localhost:3000/sse",
                "transport": "sse",
                "headers": {},
                "auth_token": None,
                "enabled": True,
            },
        ]
"""

import asyncio
import os
import sys
from contextlib import contextmanager
from typing import Any

from langchain_core.tools import BaseTool

from Django_xm.apps.core.logging_utils import get_logger

_devnull_handles: list[Any] = []


def _open_devnull():
    # devnull 句柄需保持打开供全局 stdout/stderr 替换使用，由 _close_devnull_handles 统一关闭，不能使用 with
    f = open(os.devnull, "w")  # noqa: SIM115
    _devnull_handles.append(f)
    return f


def _close_devnull_handles():
    for f in _devnull_handles:
        try:
            f.close()
        except Exception:  # noqa: S110  # cleanup, devnull 句柄关闭失败可忽略
            pass
    _devnull_handles.clear()


if not hasattr(sys.stderr, "fileno"):
    sys.stderr = sys.__stderr__ or _open_devnull()
if not hasattr(sys.stdout, "fileno"):
    sys.stdout = sys.__stdout__ or _open_devnull()

logger = get_logger(__name__)


@contextmanager
def _celery_stdio_fix():
    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr
    _opened: dict[str, Any] = {}
    try:
        if not hasattr(sys.stdout, "fileno"):
            # 句柄需保持打开并替换 sys.stdout，由本函数 finally 中统一关闭，不能使用 with
            f = open(os.devnull, "w")  # noqa: SIM115
            sys.stdout = f
            _opened["stdout"] = f
        if not hasattr(sys.stderr, "fileno"):
            f = open(os.devnull, "w")  # noqa: SIM115
            sys.stderr = f
            _opened["stderr"] = f
        yield
    finally:
        if "stdout" in _opened:
            sys.stdout = _orig_stdout
            _opened["stdout"].close()
        if "stderr" in _opened:
            sys.stderr = _orig_stderr
            _opened["stderr"].close()


@contextmanager
def _nullcontext():
    yield


import atexit

atexit.register(_close_devnull_handles)


def _get_mcp_servers_config() -> list[dict[str, Any]]:
    """系统级 MCP Server 配置。

    来源为本包的 ``config.get_system_mcp_servers()``（Task 27.3 已将配置从
    Django settings 迁至业务模块）：
        - 生产：``MCP_SYSTEM_SERVERS_JSON`` 环境变量
        - 开发：``DEFAULT_MCP_SERVERS``（sequential-thinking / context7）

    修复前此处读 ``getattr(django_settings, "MCP_SERVERS", [])``——该配置迁移后
    已不存在，导致系统级 Server 恒为空（Task 27.3 迁移遗漏的消费方切换）。
    """
    from .config import get_system_mcp_servers

    all_servers = get_system_mcp_servers()
    return [s for s in all_servers if s.get("enabled", True)]


def is_mcp_available() -> bool:
    try:
        import langchain_mcp_adapters  # noqa: F401  # 仅用于可用性检测（ImportError 表示未安装）

        return True
    except ImportError:
        return False


async def get_mcp_tools(
    server_url: str | None = None,
    server_name: str | None = None,
    transport: str = "sse",
    headers: dict[str, str] | None = None,
    auth_token: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> list[BaseTool]:
    if not is_mcp_available():
        logger.warning("langchain-mcp-adapters 未安装，无法获取 MCP 工具")
        return []

    if server_url is None and server_name is not None:
        servers = _get_mcp_servers_config()
        for srv in servers:
            if srv.get("name") == server_name:
                transport = srv.get("transport", transport)
                if transport == "stdio":
                    command = srv.get("command", command)
                    args = srv.get("args", args)
                    env = srv.get("env", env)
                else:
                    server_url = srv.get("url")
                    if not headers:
                        headers = srv.get("headers")
                    if not auth_token:
                        auth_token = srv.get("auth_token")
                break
        if server_url is None and transport != "stdio":
            logger.error(f"未找到 MCP Server 配置: {server_name}")
            return []

    if server_url is None and transport != "stdio":
        logger.error("必须提供 server_url 或 server_name（非 stdio 传输）")
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        if transport == "stdio":
            transport_config: dict[str, Any] = {
                "command": command or "npx",
                "args": args or [],
                "transport": "stdio",
            }
            if env:
                transport_config["env"] = env
            server_key = f"stdio:{command}:{':'.join(args or [])}"
        else:
            transport_config = {"url": server_url}
            if transport == "http":
                transport_config["transport"] = "http"
            elif transport == "websocket":
                transport_config["transport"] = "websocket"
            else:
                transport_config["transport"] = "sse"

            final_headers = dict(headers or {})
            if auth_token and "Authorization" not in final_headers:
                final_headers["Authorization"] = f"Bearer {auth_token}"
            if final_headers:
                transport_config["headers"] = final_headers

            server_key = f"{transport}:{server_url}"

        # langchain-mcp-adapters 0.2.x 的 MultiServerMCPClient 仅持有连接配置字典，
        # get_tools() 每次调用现场建立 session（client 对象无连接可复用、无 close()），
        # 因此不做任何客户端池化：每次现场建连，配置（含 headers/auth_token）时效性
        # 由"每次现场读取 resolve_servers 最新配置"天然保证，凭证轮换/更新即时生效。
        client = MultiServerMCPClient({server_key: transport_config})
        # 单 server 工具拉取超时（2.7：可配置，默认与原硬编码一致——
        # stdio 本地进程 15s，远程 HTTP/SSE 50s；ai_engine settings 提供）
        from Django_xm.apps.ai_engine.config import settings as ai_settings

        timeout = (
            ai_settings.mcp_tool_fetch_timeout_stdio
            if transport == "stdio"
            else ai_settings.mcp_tool_fetch_timeout_remote
        )
        with _celery_stdio_fix() if transport == "stdio" else _nullcontext():
            tools = await asyncio.wait_for(client.get_tools(), timeout=timeout)

        for tool in tools:
            if not hasattr(tool, "metadata") or tool.metadata is None:
                tool.metadata = {}
            tool.metadata["source"] = "mcp"
            tool.metadata["is_mcp_tool"] = True
            if server_name:
                tool.metadata["mcp_server_name"] = server_name

        logger.info(f"从 MCP Server 获取到 {len(tools)} 个工具 (transport={transport})")
        for tool in tools:
            logger.debug(f"  MCP 工具: {tool.name} - {tool.description[:50] if tool.description else ''}")

        return tools

    except Exception:
        logger.exception("从 MCP Server 获取工具失败")
        return []


def get_server_info_list() -> list[dict[str, Any]]:
    servers = _get_mcp_servers_config()
    info = []
    for srv in servers:
        entry = {
            "name": srv.get("name", "unknown"),
            "transport": srv.get("transport", "sse"),
            "description": srv.get("description", ""),
            "enabled": srv.get("enabled", True),
        }
        if srv.get("transport") == "stdio":
            entry["command"] = srv.get("command", "")
        else:
            entry["url"] = srv.get("url", "")
        info.append(entry)
    return info


__all__ = [
    "_get_mcp_servers_config",
    "get_mcp_tools",
    "get_server_info_list",
    "is_mcp_available",
]
