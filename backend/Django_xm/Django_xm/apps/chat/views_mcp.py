import asyncio
import json
import logging
import copy

import nest_asyncio
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.common.responses import success_response, error_response

logger = logging.getLogger(__name__)

nest_asyncio.apply()

_USER_MCP_CONFIG_KEY = "user_mcp_servers"


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _get_merged_mcp_servers():
    from django.conf import settings as django_settings
    from Django_xm.apps.cache_manager.services.cache_service import CacheService

    base_servers = list(getattr(django_settings, "MCP_SERVERS", []))
    user_servers_raw = CacheService.get(_USER_MCP_CONFIG_KEY) or []
    user_servers = user_servers_raw if isinstance(user_servers_raw, list) else []
    seen = {s.get("name") for s in base_servers}
    for srv in user_servers:
        if srv.get("name") and srv.get("name") not in seen:
            base_servers.append(srv)
            seen.add(srv.get("name"))
    return base_servers


async def _fetch_mcp_tools(selected_servers=None):
    from Django_xm.apps.tools.mcp import (
        is_mcp_available,
        get_mcp_tools,
        get_server_info_list,
        _get_local_mcp_tools,
    )

    if not is_mcp_available():
        return {
            "available": False,
            "servers": [],
            "tools": [],
            "message": "langchain-mcp-adapters 未安装",
        }

    server_info = get_server_info_list()
    all_tools = []
    servers = _get_merged_mcp_servers()

    if selected_servers:
        servers = [s for s in servers if s.get("name") in selected_servers]

    for srv in servers:
        transport = srv.get("transport", "sse")
        try:
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
                    continue
                tools = await get_mcp_tools(
                    server_url=url,
                    transport=transport,
                    headers=srv.get("headers"),
                    auth_token=srv.get("auth_token"),
                )

            for tool in tools:
                all_tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "server": srv.get("name", "unknown"),
                    "type": "remote",
                })
        except Exception as e:
            logger.warning(f"MCP Server ({srv.get('name', 'unknown')}) 工具获取失败: {e}")

    try:
        local_tools = _get_local_mcp_tools()
        for tool in local_tools:
            all_tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "server": "local",
                "type": "local",
            })
    except Exception as e:
        logger.warning(f"本地 MCP 工具获取失败: {e}")

    return {
        "available": True,
        "servers": server_info,
        "tools": all_tools,
        "total": len(all_tools),
    }


class McpToolsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            from Django_xm.apps.tools.mcp import is_mcp_available
        except ImportError:
            return success_response(data={
                "available": False,
                "servers": [],
                "tools": [],
                "message": "langchain-mcp-adapters 未安装",
            })

        selected = request.query_params.getlist("servers") or None
        data = _run_async(_fetch_mcp_tools(selected_servers=selected))
        return success_response(data=data)


class McpStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            from Django_xm.apps.tools.mcp import (
                is_mcp_available,
                get_server_info_list,
                get_pooled_client_count,
            )
            available = is_mcp_available()
            servers = _get_merged_mcp_servers() if available else []
            server_info = get_server_info_list() if available else []
            pool_count = get_pooled_client_count() if available else 0
        except ImportError:
            available = False
            servers = []
            server_info = []
            pool_count = 0

        return success_response(data={
            "available": available,
            "servers_configured": len(servers),
            "server_names": [s.get("name", "unknown") for s in servers],
            "servers": server_info,
            "pooled_clients": pool_count,
        })


class McpServerTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        server_name = request.data.get("server_name")
        if not server_name:
            return error_response(message="请提供 server_name 参数")

        servers = _get_merged_mcp_servers()
        target = None
        for srv in servers:
            if srv.get("name") == server_name:
                target = srv
                break

        if not target:
            return error_response(message=f"未找到 MCP Server: {server_name}")

        try:
            from Django_xm.apps.tools.mcp import get_mcp_tools
        except ImportError:
            return error_response(message="langchain-mcp-adapters 未安装")

        try:
            data = _run_async(_test_mcp_server(server_name, target))
            return success_response(data=data)
        except Exception as e:
            logger.error(f"MCP Server 连接测试失败 ({server_name}): {e}")
            return error_response(message=f"连接失败: {str(e)}")


async def _test_mcp_server(server_name, target):
    from Django_xm.apps.tools.mcp import get_mcp_tools

    transport = target.get("transport", "sse")
    if transport == "stdio":
        tools = await get_mcp_tools(
            server_name=server_name,
            transport="stdio",
            command=target.get("command"),
            args=target.get("args"),
            env=target.get("env"),
        )
    else:
        url = target.get("url")
        if not url:
            raise ValueError(f"MCP Server '{server_name}' 缺少 url 配置")
        tools = await get_mcp_tools(
            server_url=url,
            transport=transport,
            headers=target.get("headers"),
            auth_token=target.get("auth_token"),
        )

    tool_list = [
        {"name": t.name, "description": t.description or ""}
        for t in tools
    ]

    return {
        "server": server_name,
        "transport": transport,
        "connected": True,
        "tools": tool_list,
        "tool_count": len(tools),
    }


class McpToolCallLogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            from Django_xm.apps.tools.mcp.middleware import get_tool_call_log
            log = get_tool_call_log()
            limit = int(request.query_params.get("limit", 50))
            records = log.get_recent(limit=limit)
            return success_response(data={
                "records": records,
                "total": len(records),
            })
        except ImportError:
            return success_response(data={"records": [], "total": 0})


class McpServerListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        servers = _get_merged_mcp_servers()
        result = []
        for srv in servers:
            result.append({
                "name": srv.get("name", ""),
                "transport": srv.get("transport", "sse"),
                "url": srv.get("url", ""),
                "command": srv.get("command", ""),
                "args": srv.get("args", []),
                "description": srv.get("description", ""),
                "enabled": srv.get("enabled", True),
                "source": "system" if _is_system_server(srv.get("name")) else "user",
            })
        return success_response(data={"servers": result, "total": len(result)})


class McpServerAddView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        name = request.data.get("name")
        transport = request.data.get("transport", "sse")
        url = request.data.get("url")
        command = request.data.get("command")
        args = request.data.get("args", [])
        env = request.data.get("env")
        headers = request.data.get("headers")
        auth_token = request.data.get("auth_token")
        description = request.data.get("description", "")

        if not name:
            return error_response(message="MCP Server 名称不能为空")

        existing = _get_merged_mcp_servers()
        if any(s.get("name") == name for s in existing):
            return error_response(message=f"MCP Server '{name}' 已存在")

        new_server = {
            "name": name,
            "transport": transport,
            "description": description,
            "enabled": True,
        }
        if transport == "stdio":
            if not command:
                return error_response(message="stdio 传输必须提供 command")
            new_server["command"] = command
            new_server["args"] = args or []
            if env:
                new_server["env"] = env
        else:
            if not url:
                return error_response(message=f"{transport} 传输必须提供 url")
            new_server["url"] = url
            if headers:
                new_server["headers"] = headers
            if auth_token:
                new_server["auth_token"] = auth_token

        from Django_xm.apps.cache_manager.services.cache_service import CacheService
        user_servers = CacheService.get(_USER_MCP_CONFIG_KEY) or []
        if not isinstance(user_servers, list):
            user_servers = []
        user_servers.append(new_server)
        CacheService.set(_USER_MCP_CONFIG_KEY, user_servers, timeout=None)

        logger.info(f"用户添加 MCP Server: {name} (transport={transport})")
        return success_response(data=new_server, message=f"MCP Server '{name}' 添加成功")


class McpServerDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        name = request.data.get("name")
        if not name:
            return error_response(message="请提供要删除的 MCP Server 名称")

        if _is_system_server(name):
            return error_response(message=f"系统内置 MCP Server '{name}' 不可删除")

        from Django_xm.apps.cache_manager.services.cache_service import CacheService
        user_servers = CacheService.get(_USER_MCP_CONFIG_KEY) or []
        if not isinstance(user_servers, list):
            user_servers = []
        original_len = len(user_servers)
        user_servers = [s for s in user_servers if s.get("name") != name]

        if len(user_servers) == original_len:
            return error_response(message=f"未找到用户自定义 MCP Server: {name}")

        CacheService.set(_USER_MCP_CONFIG_KEY, user_servers, timeout=None)
        logger.info(f"用户删除 MCP Server: {name}")
        return success_response(message=f"MCP Server '{name}' 已删除")


class ToolListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            from Django_xm.apps.tools import get_all_available_tool_info
            tools = get_all_available_tool_info()
            return success_response(data={"tools": tools, "total": len(tools)})
        except Exception as e:
            logger.error(f"获取工具列表失败: {e}")
            return error_response(message=f"获取工具列表失败: {str(e)}")


class ToolUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tool_name = request.data.get("name")
        tool_code = request.data.get("code")
        description = request.data.get("description", "")

        if not tool_name or not tool_code:
            return error_response(message="工具名称和代码不能为空")

        try:
            exec_globals = {"__builtins__": {}}
            safe = False
            try:
                from RestrictedPython import compile_restricted
                from RestrictedPython.Guards import safe_builtins
                byte_code = compile_restricted(tool_code, filename="<user_tool>", mode="exec")
                if byte_code.errors:
                    return error_response(message=f"代码安全检查未通过: {'; '.join(byte_code.errors)}")
                exec_globals = {"__builtins__": safe_builtins}
                safe = True
            except ImportError:
                _UNSAFE_PATTERNS = [
                    "import os", "import sys", "import subprocess", "__import__",
                    "open(", "exec(", "eval(", "compile(",
                    "globals()", "locals()", "getattr(", "setattr(", "delattr(",
                    "os.", "sys.", "subprocess.",
                ]
                for pattern in _UNSAFE_PATTERNS:
                    if pattern in tool_code:
                        return error_response(message=f"代码包含不安全操作: {pattern}，请移除后重试")
                safe = True

            if not safe:
                return error_response(message="代码安全检查未通过")

            exec(tool_code, exec_globals)

            tool_instance = None
            from langchain_core.tools import BaseTool
            for obj in exec_globals.values():
                if isinstance(obj, BaseTool):
                    tool_instance = obj
                    break
                if isinstance(obj, type) and issubclass(obj, BaseTool) and obj is not BaseTool:
                    tool_instance = obj()
                    break

            if not tool_instance:
                return error_response(message="代码中未找到有效的 LangChain BaseTool 实例")

            from Django_xm.apps.cache_manager.services.cache_service import CacheService
            user_tools = CacheService.get("user_custom_tools") or []
            if not isinstance(user_tools, list):
                user_tools = []
            user_tools.append({
                "name": tool_name,
                "description": description or tool_instance.description or "",
                "code": tool_code,
            })
            CacheService.set("user_custom_tools", user_tools, timeout=None)

            logger.info(f"用户上传自定义工具: {tool_name}")
            return success_response(data={
                "name": tool_name,
                "description": description or tool_instance.description or "",
            }, message=f"工具 '{tool_name}' 上传成功")

        except Exception as e:
            logger.error(f"工具上传失败: {e}")
            return error_response(message=f"工具上传失败: {str(e)}")


def _is_system_server(name):
    from django.conf import settings as django_settings
    system_servers = getattr(django_settings, "MCP_SERVERS", [])
    return any(s.get("name") == name for s in system_servers)
