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
        MCP_LOCAL_TOOLS_ENABLED = True
"""

from typing import List, Dict, Any, Optional, Sequence, Callable
from langchain_core.tools import BaseTool

from Django_xm.apps.ai_engine.config import settings, get_logger

logger = get_logger(__name__)

_mcp_client_pool: Dict[str, Any] = {}
_mcp_pool_lock: Optional[Any] = None


def _get_pool_lock():
    global _mcp_pool_lock
    if _mcp_pool_lock is None:
        import threading
        _mcp_pool_lock = threading.Lock()
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
        except Exception:
            pass
    except Exception:
        pass


import atexit
atexit.register(_sync_cleanup_on_exit)


def _get_mcp_servers_config() -> List[Dict[str, Any]]:
    from django.conf import settings as django_settings
    all_servers = getattr(django_settings, "MCP_SERVERS", [])
    return [s for s in all_servers if s.get("enabled", True)]


def is_mcp_available() -> bool:
    try:
        import langchain_mcp_adapters  # noqa: F401
        return True
    except ImportError:
        return False


def _build_transport_config(server_config: Dict[str, Any]) -> Dict[str, Any]:
    transport = server_config.get("transport", "sse")

    if transport == "stdio":
        config: Dict[str, Any] = {
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
    transport_config: Dict[str, Any],
) -> Any:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    with _get_pool_lock():
        if server_key in _mcp_client_pool:
            return _mcp_client_pool[server_key]

        client = MultiServerMCPClient({server_key: transport_config})
        _mcp_client_pool[server_key] = client
        logger.info(f"MCP 客户端已创建并缓存: {server_key}")
        return client


async def _remove_stale_client(server_key: str) -> None:
    with _get_pool_lock():
        if server_key in _mcp_client_pool:
            old_client = _mcp_client_pool.pop(server_key)
            try:
                if hasattr(old_client, 'close'):
                    await old_client.close()
            except Exception:
                pass
            logger.info(f"MCP 过期客户端已移除: {server_key}")


async def cleanup_mcp_clients() -> None:
    with _get_pool_lock():
        _mcp_client_pool.clear()
        logger.info("MCP 客户端池已清理")


def get_pooled_client_count() -> int:
    return len(_mcp_client_pool)


async def get_mcp_tools(
    server_url: Optional[str] = None,
    server_name: Optional[str] = None,
    transport: str = "sse",
    headers: Optional[Dict[str, str]] = None,
    auth_token: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    interceptors: Optional[List[Callable]] = None,
) -> List[BaseTool]:
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
            transport_config: Dict[str, Any] = {
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

        client = await _get_or_create_client(server_key, transport_config)
        try:
            tools = await client.get_tools()
        except Exception as tool_err:
            logger.warning(f"MCP 客户端 get_tools 失败，尝试重建连接: {tool_err}")
            await _remove_stale_client(server_key)
            client = await _get_or_create_client(server_key, transport_config)
            tools = await client.get_tools()

        if interceptors:
            tools = _apply_interceptors(tools, interceptors)

        logger.info(f"从 MCP Server 获取到 {len(tools)} 个工具 (transport={transport})")
        for tool in tools:
            logger.debug(f"  MCP 工具: {tool.name} - {tool.description[:50] if tool.description else ''}")

        return tools

    except Exception as e:
        if 'server_key' in dir() and server_key in _mcp_client_pool:
            await _remove_stale_client(server_key)
        logger.error(f"从 MCP Server 获取工具失败: {e}")
        return []


async def get_mcp_resources(
    server_url: Optional[str] = None,
    server_name: Optional[str] = None,
    transport: str = "sse",
    headers: Optional[Dict[str, str]] = None,
    auth_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not is_mcp_available():
        return []

    if server_url is None and server_name is not None:
        servers = _get_mcp_servers_config()
        for srv in servers:
            if srv.get("name") == server_name:
                server_url = srv.get("url")
                transport = srv.get("transport", transport)
                if not headers:
                    headers = srv.get("headers")
                if not auth_token:
                    auth_token = srv.get("auth_token")
                break
        if server_url is None:
            return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        transport_config: Dict[str, Any] = {"url": server_url}
        if transport == "http":
            transport_config["transport"] = "http"
        else:
            transport_config["transport"] = "sse"

        final_headers = dict(headers or {})
        if auth_token and "Authorization" not in final_headers:
            final_headers["Authorization"] = f"Bearer {auth_token}"
        if final_headers:
            transport_config["headers"] = final_headers

        server_key = f"{transport}:{server_url}"
        client = await _get_or_create_client(server_key, transport_config)

        resources = []
        if hasattr(client, 'list_resources'):
            try:
                resources = await client.list_resources()
                logger.info(f"从 MCP Server ({server_url}) 获取到 {len(resources)} 个 Resources")
            except Exception as e:
                logger.debug(f"获取 Resources 不支持或失败: {e}")

        return resources

    except Exception as e:
        logger.error(f"从 MCP Server 获取 Resources 失败: {e}")
        return []


async def get_mcp_prompts(
    server_url: Optional[str] = None,
    server_name: Optional[str] = None,
    transport: str = "sse",
    headers: Optional[Dict[str, str]] = None,
    auth_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not is_mcp_available():
        return []

    if server_url is None and server_name is not None:
        servers = _get_mcp_servers_config()
        for srv in servers:
            if srv.get("name") == server_name:
                server_url = srv.get("url")
                transport = srv.get("transport", transport)
                if not headers:
                    headers = srv.get("headers")
                if not auth_token:
                    auth_token = srv.get("auth_token")
                break
        if server_url is None:
            return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        transport_config: Dict[str, Any] = {"url": server_url}
        if transport == "http":
            transport_config["transport"] = "http"
        else:
            transport_config["transport"] = "sse"

        final_headers = dict(headers or {})
        if auth_token and "Authorization" not in final_headers:
            final_headers["Authorization"] = f"Bearer {auth_token}"
        if final_headers:
            transport_config["headers"] = final_headers

        server_key = f"{transport}:{server_url}"
        client = await _get_or_create_client(server_key, transport_config)

        prompts = []
        if hasattr(client, 'list_prompts'):
            try:
                prompts = await client.list_prompts()
                logger.info(f"从 MCP Server ({server_url}) 获取到 {len(prompts)} 个 Prompts")
            except Exception as e:
                logger.debug(f"获取 Prompts 不支持或失败: {e}")

        return prompts

    except Exception as e:
        logger.error(f"从 MCP Server 获取 Prompts 失败: {e}")
        return []


def _apply_interceptors(
    tools: List[BaseTool],
    interceptors: List[Callable],
) -> List[BaseTool]:
    from langchain_core.tools import tool as lc_tool

    wrapped_tools = []
    for original_tool in tools:
        original_name = original_tool.name
        original_description = original_tool.description or ""

        def _make_wrapper(ot, on):
            @lc_tool(name=on, description=original_description)
            def wrapped_tool(**kwargs):
                for interceptor in interceptors:
                    kwargs = interceptor(on, kwargs) or kwargs
                return ot.invoke(kwargs)

            return wrapped_tool

        wrapped_tools.append(_make_wrapper(original_tool, original_name))

    return wrapped_tools


async def get_all_mcp_tools() -> List[BaseTool]:
    if not is_mcp_available():
        return []

    servers = _get_mcp_servers_config()
    if not servers:
        logger.debug("未配置 MCP Server")
        return []

    all_tools: List[BaseTool] = []

    for srv in servers:
        name = srv.get("name", "unknown")
        transport = srv.get("transport", "sse")

        if transport == "stdio":
            tools = await get_mcp_tools(
                server_name=name,
                transport="stdio",
                command=srv.get("command"),
                args=srv.get("args"),
                env=srv.get("env"),
            )
        else:
            url = srv.get("url")
            if not url:
                logger.warning(f"MCP Server '{name}' 缺少 url 配置，跳过")
                continue
            tools = await get_mcp_tools(
                server_url=url,
                transport=transport,
                headers=srv.get("headers"),
                auth_token=srv.get("auth_token"),
            )

        all_tools.extend(tools)

    local_tools = _get_local_mcp_tools()
    if local_tools:
        all_tools.extend(local_tools)
        logger.info(f"本地自定义 MCP 工具已加载 ({len(local_tools)} 个)")

    logger.info(f"共获取 {len(all_tools)} 个 MCP 工具（来自 {len(servers)} 个 Server + 本地工具）")
    return all_tools


async def get_all_mcp_resources() -> List[Dict[str, Any]]:
    if not is_mcp_available():
        return []

    servers = _get_mcp_servers_config()
    all_resources: List[Dict[str, Any]] = []

    for srv in servers:
        name = srv.get("name", "unknown")
        url = srv.get("url")
        if not url:
            continue

        resources = await get_mcp_resources(
            server_url=url,
            transport=srv.get("transport", "sse"),
            headers=srv.get("headers"),
            auth_token=srv.get("auth_token"),
        )
        all_resources.extend(resources)

    return all_resources


async def get_all_mcp_prompts() -> List[Dict[str, Any]]:
    if not is_mcp_available():
        return []

    servers = _get_mcp_servers_config()
    all_prompts: List[Dict[str, Any]] = []

    for srv in servers:
        name = srv.get("name", "unknown")
        url = srv.get("url")
        if not url:
            continue

        prompts = await get_mcp_prompts(
            server_url=url,
            transport=srv.get("transport", "sse"),
            headers=srv.get("headers"),
            auth_token=srv.get("auth_token"),
        )
        all_prompts.extend(prompts)

    return all_prompts


def _get_local_mcp_tools() -> List[BaseTool]:
    from django.conf import settings as django_settings
    if not getattr(django_settings, "MCP_LOCAL_TOOLS_ENABLED", False):
        return []

    try:
        from .local import get_local_mcp_tools
        return get_local_mcp_tools()
    except ImportError:
        logger.debug("本地 MCP 工具模块未找到")
        return []
    except Exception as e:
        logger.warning(f"加载本地 MCP 工具失败: {e}")
        return []


def get_mcp_tools_sync(
    server_url: Optional[str] = None,
    server_name: Optional[str] = None,
    transport: str = "sse",
    headers: Optional[Dict[str, str]] = None,
    auth_token: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
) -> List[BaseTool]:
    import asyncio

    async def _inner():
        return await get_mcp_tools(
            server_url=server_url,
            server_name=server_name,
            transport=transport,
            headers=headers,
            auth_token=auth_token,
            command=command,
            args=args,
            env=env,
        )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            try:
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(_inner())
            except ImportError:
                logger.warning("在已有事件循环中调用同步 MCP 获取，且 nest_asyncio 未安装，返回空列表")
                return []
        return loop.run_until_complete(_inner())
    except RuntimeError:
        return asyncio.run(_inner())


def get_server_info_list() -> List[Dict[str, Any]]:
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
    "is_mcp_available",
    "get_mcp_tools",
    "get_mcp_resources",
    "get_mcp_prompts",
    "get_all_mcp_tools",
    "get_all_mcp_resources",
    "get_all_mcp_prompts",
    "get_mcp_tools_sync",
    "cleanup_mcp_clients",
    "get_pooled_client_count",
    "get_server_info_list",
    "_get_mcp_servers_config",
]
