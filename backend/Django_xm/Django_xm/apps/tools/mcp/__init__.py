"""
MCP (Model Context Protocol) 工具集成模块

通过 langchain-mcp-adapters 将 MCP Server 的工具转换为 LangChain BaseTool，
使 Agent 可以调用外部 MCP 服务提供的工具。

支持传输协议: stdio / sse / http / websocket
支持远程服务器 + 本地自定义 MCP 工具
支持 LangGraph 和 LangChain Agent 两种框架集成

配置:
    settings.py 中配置:
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
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel

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


_mcp_client_pool: dict[str, Any] = {}
_mcp_client_timestamps: dict[str, float] = {}
_mcp_pool_lock: Any | None = None
_MCP_CLIENT_TTL_SECONDS = 600


import threading

_mcp_pool_lock = threading.Lock()


def _get_pool_lock():
    return _mcp_pool_lock


def _sync_cleanup_on_exit():
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return
        loop.run_until_complete(cleanup_mcp_clients())
    except RuntimeError:
        try:
            asyncio.run(cleanup_mcp_clients())
        except Exception:  # noqa: S110  # cleanup, atexit 时 MCP 客户端清理失败可忽略
            pass
    except Exception:  # noqa: S110  # cleanup, atexit 时事件循环清理失败可忽略
        pass


import atexit

atexit.register(_sync_cleanup_on_exit)
atexit.register(_close_devnull_handles)


def _get_mcp_servers_config() -> list[dict[str, Any]]:
    from django.conf import settings as django_settings

    all_servers = getattr(django_settings, "MCP_SERVERS", [])
    return [s for s in all_servers if s.get("enabled", True)]


def is_mcp_available() -> bool:
    try:
        import langchain_mcp_adapters  # noqa: F401  # 仅用于可用性检测（ImportError 表示未安装）

        return True
    except ImportError:
        return False


def _build_transport_config(server_config: dict[str, Any]) -> dict[str, Any]:
    transport = server_config.get("transport", "sse")

    if transport == "stdio":
        config: dict[str, Any] = {
            "command": server_config.get("command", ""),
            "args": server_config.get("args", []),
            "transport": "stdio",
        }
        env = server_config.get("env")
        if env:
            config["env"] = env
        return config

    url = server_config.get("url", "")
    headers = dict(server_config.get("headers", {}))

    auth_token = server_config.get("auth_token")
    if auth_token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {auth_token}"

    config = {"url": url}

    if transport == "http":
        config["transport"] = "http"
    elif transport == "websocket":
        config["transport"] = "websocket"
    else:
        config["transport"] = "sse"

    if headers:
        config["headers"] = headers

    timeout = server_config.get("timeout")
    if timeout:
        config["timeout"] = timeout

    return config


async def _get_or_create_client(
    server_key: str,
    transport_config: dict[str, Any],
    server_name: str | None = None,
) -> Any:
    import time

    from langchain_mcp_adapters.client import MultiServerMCPClient

    with _get_pool_lock():
        if server_key in _mcp_client_pool:
            created_at = _mcp_client_timestamps.get(server_key, 0)
            if time.time() - created_at > _MCP_CLIENT_TTL_SECONDS:
                old_client = _mcp_client_pool.pop(server_key)
                _mcp_client_timestamps.pop(server_key, None)
                try:
                    if hasattr(old_client, "close"):
                        await old_client.close()
                except Exception:  # noqa: S110  # cleanup, 过期 MCP 客户端关闭失败可忽略
                    pass
                logger.info(f"MCP 过期客户端已移除 (TTL={_MCP_CLIENT_TTL_SECONDS}s): {server_key}")
            else:
                return _mcp_client_pool[server_key]

        client = MultiServerMCPClient({server_key: transport_config})
        _mcp_client_pool[server_key] = client
        _mcp_client_timestamps[server_key] = time.time()
        logger.info(f"MCP 客户端已创建并缓存: {server_key}")
        return client


async def _remove_stale_client(server_key: str) -> None:
    with _get_pool_lock():
        if server_key in _mcp_client_pool:
            old_client = _mcp_client_pool.pop(server_key)
            _mcp_client_timestamps.pop(server_key, None)
            try:
                if hasattr(old_client, "close"):
                    await old_client.close()
            except Exception:  # noqa: S110  # cleanup, 失效 MCP 客户端关闭失败可忽略
                pass
            logger.info(f"MCP 过期客户端已移除: {server_key}")


async def cleanup_mcp_clients() -> None:
    with _get_pool_lock():
        for key, client in list(_mcp_client_pool.items()):
            try:
                if hasattr(client, "close"):
                    await client.close()
            except Exception as e:
                logger.warning(f"关闭 MCP 客户端 {key} 失败: {e}")
        _mcp_client_pool.clear()
        _mcp_client_timestamps.clear()
        logger.info("MCP 客户端池已清理")


def get_pooled_client_count() -> int:
    return len(_mcp_client_pool)


async def get_mcp_tools(
    server_url: str | None = None,
    server_name: str | None = None,
    transport: str = "sse",
    headers: dict[str, str] | None = None,
    auth_token: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    interceptors: list[Callable] | None = None,
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
        from langchain_mcp_adapters.client import (
            MultiServerMCPClient,  # noqa: F401  # 延迟导入 + 可用性检测，实际使用在 _get_or_create_client
        )

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

        client = await _get_or_create_client(server_key, transport_config, server_name=server_name)
        _need_stdio_fix = transport == "stdio"
        # 根据传输类型设置超时：stdio 本地进程 15s，远程 HTTP/SSE 50s
        _timeout = 15 if transport == "stdio" else 50
        try:
            with _celery_stdio_fix() if _need_stdio_fix else _nullcontext():
                tools = await asyncio.wait_for(client.get_tools(), timeout=_timeout)
        except TimeoutError:
            logger.warning(f"MCP 客户端 get_tools 超时 ({_timeout}s)，尝试重建连接: {server_key}")
            await _remove_stale_client(server_key)
            client = await _get_or_create_client(server_key, transport_config, server_name=server_name)
            with _celery_stdio_fix() if _need_stdio_fix else _nullcontext():
                tools = await asyncio.wait_for(client.get_tools(), timeout=_timeout)
        except BaseException as tool_err:
            logger.warning(f"MCP 客户端 get_tools 失败，尝试重建连接: {tool_err}")
            await _remove_stale_client(server_key)
            try:
                client = await _get_or_create_client(server_key, transport_config, server_name=server_name)
                with _celery_stdio_fix() if _need_stdio_fix else _nullcontext():
                    tools = await asyncio.wait_for(client.get_tools(), timeout=_timeout)
            except BaseException:
                logger.exception("MCP 客户端重建后 get_tools 仍失败")
                raise

        if interceptors:
            tools = _apply_interceptors(tools, interceptors)

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
        if "server_key" in dir() and server_key in _mcp_client_pool:
            await _remove_stale_client(server_key)
        logger.exception("从 MCP Server 获取工具失败")
        return []


def _apply_interceptors(
    tools: list[BaseTool],
    interceptors: list[Callable],
) -> list[BaseTool]:
    from langchain_core.tools import tool as lc_tool

    wrapped_tools = []
    for original_tool in tools:
        original_name = original_tool.name
        original_description = original_tool.description or ""

        def _make_wrapper(ot, on, od):
            @lc_tool(name=on, description=od)
            def wrapped_tool(**kwargs):
                for interceptor in interceptors:
                    kwargs = interceptor(on, kwargs) or kwargs
                return ot.invoke(kwargs)

            return wrapped_tool

        wrapped_tools.append(_make_wrapper(original_tool, original_name, original_description))

    return wrapped_tools


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
    "cleanup_mcp_clients",
    "get_mcp_tools",
    "get_pooled_client_count",
    "get_server_info_list",
    "is_mcp_available",
]
