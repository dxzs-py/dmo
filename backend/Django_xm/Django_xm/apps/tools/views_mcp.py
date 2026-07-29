import logging

from asgiref.sync import sync_to_async
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.throttling import SensitiveOperationRateThrottle
from Django_xm.apps.tools.managers import _build_user_tool_category_info
from Django_xm.apps.tools.views_common import (
    StandardPagination,
    _handle_crud_result,
)
from Django_xm.async_utils import run_async
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import error_response, success_response
from Django_xm.common.serializers import EmptySerializer

logger = logging.getLogger(__name__)


def _get_merged_mcp_servers(user=None):
    """合并系统级和用户级 MCP Server 配置

    系统级：settings.MCP_SERVERS（所有用户共享）
    用户级：数据库 McpServerConfig（仅当前用户可见）
    """
    from django.conf import settings as django_settings

    from Django_xm.apps.tools.models import McpServerConfig

    base_servers = list(getattr(django_settings, "MCP_SERVERS", []))
    seen = {s.get("name") for s in base_servers}

    if user and user.is_authenticated:
        user_servers = McpServerConfig.objects.filter(user=user, status="active")
        for srv in user_servers:
            if srv.name not in seen:
                base_servers.append(srv.to_config_dict())
                seen.add(srv.name)

    return base_servers


async def _fetch_mcp_tools(selected_servers=None, user=None):
    from Django_xm.apps.tools.mcp import (
        get_mcp_tools,
        get_server_info_list,
        is_mcp_available,
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
    servers = await sync_to_async(_get_merged_mcp_servers)(user=user)

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
                all_tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "server": srv.get("name", "unknown"),
                        "type": "remote",
                        "visibility": "selectable",
                        "tier": "extended",
                    }
                )
        except Exception as e:
            logger.warning(f"MCP Server ({srv.get('name', 'unknown')}) 工具获取失败: {e}")

    return {
        "available": True,
        "servers": server_info,
        "tools": all_tools,
        "total": len(all_tools),
    }


class McpToolsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            from Django_xm.apps.tools.mcp import is_mcp_available
        except ImportError:
            return success_response(
                data={
                    "available": False,
                    "servers": [],
                    "tools": [],
                    "message": "langchain-mcp-adapters 未安装",
                }
            )

        selected = request.query_params.getlist("servers") or None
        data = run_async(_fetch_mcp_tools(selected_servers=selected, user=request.user))
        tools = data.get("tools", [])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(tools, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return success_response(data=data)


class McpStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            from Django_xm.apps.tools.mcp import (
                get_pooled_client_count,
                get_server_info_list,
                is_mcp_available,
            )

            available = is_mcp_available()
            servers = _get_merged_mcp_servers(user=request.user) if available else []
            server_info = get_server_info_list() if available else []
            pool_count = get_pooled_client_count() if available else 0
        except ImportError:
            available = False
            servers = []
            server_info = []
            pool_count = 0

        return success_response(
            data={
                "available": available,
                "servers_configured": len(servers),
                "server_names": [s.get("name", "unknown") for s in servers],
                "servers": server_info,
                "pooled_clients": pool_count,
            }
        )


class McpServerTestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        server_name = request.data.get("server_name")
        if not server_name:
            return error_response(message="请提供 server_name 参数")

        servers = _get_merged_mcp_servers(user=request.user)
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
            data = run_async(_test_mcp_server(server_name, target))
            return success_response(data=data)
        except Exception as e:
            logger.exception(f"MCP Server 连接测试失败 ({server_name})")
            return error_response(message=f"连接失败: {e!s}")


async def _test_mcp_server(server_name, target):
    import asyncio

    from Django_xm.apps.tools.mcp import get_mcp_tools

    transport = target.get("transport", "sse")
    timeout = target.get("timeout", 20)

    try:
        if transport == "stdio":
            tools = await asyncio.wait_for(
                get_mcp_tools(
                    server_name=server_name,
                    transport="stdio",
                    command=target.get("command"),
                    args=target.get("args"),
                    env=target.get("env"),
                ),
                timeout=timeout,
            )
        else:
            url = target.get("url")
            if not url:
                raise ValueError(f"MCP Server '{server_name}' 缺少 url 配置")
            tools = await asyncio.wait_for(
                get_mcp_tools(
                    server_url=url,
                    transport=transport,
                    headers=target.get("headers"),
                    auth_token=target.get("auth_token"),
                ),
                timeout=timeout,
            )
    except TimeoutError:
        return {
            "server": server_name,
            "transport": transport,
            "connected": False,
            "error": f"连接超时 ({timeout}s)",
            "tools": [],
            "tool_count": 0,
        }
    except Exception as e:
        return {
            "server": server_name,
            "transport": transport,
            "connected": False,
            "error": str(e),
            "tools": [],
            "tool_count": 0,
        }

    tool_list = [{"name": t.name, "description": t.description or ""} for t in tools]

    return {
        "server": server_name,
        "transport": transport,
        "connected": True,
        "tools": tool_list,
        "tool_count": len(tools),
    }


class McpToolCallLogView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            from Django_xm.apps.tools.mcp.middleware import get_tool_call_log

            log = get_tool_call_log()
            limit = int(request.query_params.get("limit", 50))
            records = log.get_recent(limit=limit)
            paginator = StandardPagination()
            page = paginator.paginate_queryset(records, request)
            if page is not None:
                return paginator.get_paginated_response(page)
            return success_response(
                data={
                    "records": records,
                    "total": len(records),
                }
            )
        except ImportError:
            return success_response(data={"records": [], "total": 0})


class McpServerListView(APIView):
    """MCP Server 列表视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.managers import McpToolManager

        manager = McpToolManager()
        try:
            tools = manager.get_tool_info_list(request.user)
            paginator = StandardPagination()
            page = paginator.paginate_queryset(tools, request)
            if page is not None:
                return paginator.get_paginated_response(page)
            return success_response(data={"servers": tools, "total": len(tools)})
        except Exception as e:
            logger.exception("获取 MCP Server 列表失败")
            return error_response(message=f"获取 MCP Server 列表失败: {e!s}")


class McpServerAddView(APIView):
    """添加用户级 MCP Server 视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import McpToolManager
        from Django_xm.apps.tools.serializers import McpServerAddSerializer

        serializer = McpServerAddSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = McpToolManager()
        result = manager.add_server(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get("message", "MCP Server 添加成功"))


class McpServerUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import McpToolManager
        from Django_xm.apps.tools.serializers import McpServerAddSerializer

        serializer = McpServerAddSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = McpToolManager()
        result = manager.update_server(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get("message", "MCP Server 更新成功"))


class McpServerDeleteView(APIView):
    """删除用户级 MCP Server 视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import McpToolManager
        from Django_xm.apps.tools.serializers import McpServerDeleteSerializer

        serializer = McpServerDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        name = serializer.validated_data["name"]
        manager = McpToolManager()
        result = manager.delete_user_tool(name, request.user)
        return _handle_crud_result(result, result.get("message", "MCP Server 已删除"))


class McpServerToggleView(APIView):
    """启用/禁用用户级 MCP Server 视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import McpToolManager
        from Django_xm.apps.tools.serializers import McpServerDeleteSerializer

        serializer = McpServerDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        name = serializer.validated_data["name"]
        status = request.data.get("status")
        manager = McpToolManager()
        result = manager.toggle_user_tool(name, request.user, status)
        return _handle_crud_result(
            result,
            result.get("message", "状态切换成功"),
            data={"name": name, "status": result.get("status")},
        )


class ToolListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.managers import (
            LangChainToolManager,
            McpToolManager,
            SkillToolManager,
        )

        tool_type = request.query_params.get("type", "").strip()
        user = request.user

        managers = {
            "langchain": LangChainToolManager(),
            "mcp": McpToolManager(),
            "skill": SkillToolManager(),
        }

        if tool_type:
            if tool_type not in managers:
                return error_response(
                    message=f"无效的工具类型 '{tool_type}'，可选值: langchain, mcp, skill",
                    code=ErrorCode.VALIDATION_FAILED,
                )
            try:
                tools = managers[tool_type].get_tool_info_list(user)
                paginator = StandardPagination()
                page = paginator.paginate_queryset(tools, request)
                if page is not None:
                    return paginator.get_paginated_response(page)
                return success_response(data={tool_type: tools, "total": len(tools)})
            except Exception as e:
                logger.exception(f"获取 {tool_type} 工具列表失败")
                return error_response(message=f"获取工具列表失败: {e!s}")

        result = {}
        total = 0
        for t, manager in managers.items():
            try:
                tools = manager.get_tool_info_list(user)
                result[t] = tools
                total += len(tools)
            except Exception:
                logger.exception(f"获取 {t} 工具列表失败")
                result[t] = []

        all_tools = []
        for tools in result.values():
            all_tools.extend(tools)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(all_tools, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return success_response(data={**result, "total": total})


class ToolUploadView(APIView):
    """自定义工具上传视图

    用户上传的 @tool 装饰器代码保存到数据库，默认审核状态为 pending
    （等待管理员审核通过后才生效，approval_status='approved'）。
    运行时通过受限沙箱动态加载为 BaseTool 实例。

    限流：SensitiveOperationRateThrottle（scope='sensitive'）防止恶意刷上传。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [SensitiveOperationRateThrottle]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.models import CustomTool, ToolCategory
        from Django_xm.apps.tools.serializers import McpToolUploadSerializer

        serializer = McpToolUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )
        data = serializer.validated_data
        tool_name = data["name"]
        tool_code = data["code"]
        description = data.get("description", "")
        category_code = data.get("category", "general")

        if "@tool" not in tool_code:
            return error_response(message="代码必须包含 @tool 装饰器定义的 LangChain 工具")

        if CustomTool.objects.filter(user=request.user, name=tool_name).exists():
            return error_response(message=f"工具 '{tool_name}' 已存在")

        try:
            compile(tool_code, f"<tool:{tool_name}>", "exec")
        except SyntaxError as e:
            return error_response(message=f"代码语法错误: {e.msg} (行 {e.lineno})")

        try:
            category = ToolCategory.objects.get(code=category_code)
        except ToolCategory.DoesNotExist:
            category = ToolCategory.objects.get(code="general")

        try:
            tool_obj = CustomTool.objects.create(
                user=request.user,
                name=tool_name,
                description=description,
                code=tool_code,
                tool_type="langchain",
                category=category,
                source="user",
                status="active",
                # 默认 approval_status='pending'，需管理员审核通过后才生效
            )
            logger.info(
                f"用户上传自定义工具: {tool_name} (user={request.user.id}, approval_status={tool_obj.approval_status})"
            )
            return success_response(
                data={
                    "id": tool_obj.id,
                    "name": tool_obj.name,
                    "description": tool_obj.description,
                    "status": tool_obj.status,
                    "approval_status": tool_obj.approval_status,
                },
                message=f"工具 '{tool_name}' 上传成功，等待管理员审核",
            )
        except Exception as e:
            logger.exception("工具上传失败")
            return error_response(message=f"工具上传失败: {e!s}")


class CustomToolListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.models import CustomTool

        tools = CustomTool.objects.filter(user=request.user).select_related("category")
        data = [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "tool_type": t.tool_type,
                "category": _build_user_tool_category_info(t),
                "source": t.source,
                "status": t.status,
                "visibility": "selectable",
                "tier": "extended",
                "created_at": t.created_at.isoformat(),
            }
            for t in tools
        ]
        paginator = StandardPagination()
        page = paginator.paginate_queryset(data, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return success_response(data={"tools": data, "total": len(data)})


class CustomToolDeleteView(APIView):
    """删除自定义工具"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import LangChainToolManager

        tool_name = request.data.get("name")
        if not tool_name:
            return error_response(message="请提供 name 参数")

        manager = LangChainToolManager()
        result = manager.delete_user_tool(tool_name, request.user)
        return _handle_crud_result(result, result.get("message", "自定义工具已删除"))


class CustomToolToggleView(APIView):
    """启用/禁用自定义工具"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import LangChainToolManager

        tool_name = request.data.get("name")
        if not tool_name:
            return error_response(message="请提供 name 参数")

        status = request.data.get("status")
        manager = LangChainToolManager()
        result = manager.toggle_user_tool(tool_name, request.user, status)
        return _handle_crud_result(
            result,
            result.get("message", "状态切换成功"),
            data={"name": tool_name, "status": result.get("status")},
        )


class CustomToolUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.models import CustomTool, ToolCategory

        tool_name = request.data.get("name")
        if not tool_name:
            return error_response(message="请提供 name 参数")

        tool_obj = CustomTool.objects.filter(user=request.user, name=tool_name).first()
        if not tool_obj:
            return error_response(message=f"未找到自定义工具 '{tool_name}'")

        new_code = request.data.get("code")
        new_desc = request.data.get("description")
        new_category = request.data.get("category")

        if new_code is not None:
            if "@tool" not in new_code:
                return error_response(message="代码必须包含 @tool 装饰器定义的 LangChain 工具")
            try:
                compile(new_code, f"<tool:{tool_name}>", "exec")
            except SyntaxError as e:
                return error_response(message=f"代码语法错误: {e.msg} (行 {e.lineno})")
            tool_obj.code = new_code

        if new_desc is not None:
            tool_obj.description = new_desc

        if new_category is not None:
            try:
                tool_obj.category = ToolCategory.objects.get(code=new_category)
            except ToolCategory.DoesNotExist:
                pass

        tool_obj.save()
        logger.info(f"自定义工具 '{tool_name}' 已更新 (user={request.user.id})")
        return success_response(
            data={
                "name": tool_obj.name,
                "description": tool_obj.description,
                "status": tool_obj.status,
            },
            message=f"工具 '{tool_name}' 更新成功",
        )


class McpServerDiscoverView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.serializers import McpServerDiscoverSerializer

        serializer = McpServerDiscoverSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )
        registry_url = serializer.validated_data["registry_url"]

        try:
            from Django_xm.apps.tools.mcp.discovery import get_mcp_discovery

            discovery = get_mcp_discovery()
            servers = run_async(discovery.discover_from_registry(registry_url))
            return success_response(
                data={
                    "registry_url": registry_url,
                    "servers": servers,
                    "total": len(servers),
                }
            )
        except Exception as e:
            logger.exception(f"MCP Server 发现失败 ({registry_url})")
            return error_response(message=f"发现失败: {e!s}")


# ---------------------------------------------------------------------------
# Skills 视图
# ---------------------------------------------------------------------------


class SkillListView(APIView):
    """Skill 列表视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.managers import SkillToolManager

        manager = SkillToolManager()
        try:
            skills = manager.get_tool_info_list(request.user)
            return success_response(data={"skills": skills, "total": len(skills)})
        except Exception as e:
            logger.exception("获取 Skill 列表失败")
            return error_response(message=f"获取 Skill 列表失败: {e!s}")


class SkillCreateView(APIView):
    """创建自定义 Skill 视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import SkillToolManager
        from Django_xm.apps.tools.serializers import SkillCreateSerializer

        serializer = SkillCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = SkillToolManager()
        result = manager.create_skill(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get("message", "Skill 创建成功"))


class SkillDeleteView(APIView):
    """删除自定义 Skill 视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import SkillToolManager
        from Django_xm.apps.tools.serializers import SkillDeleteSerializer

        serializer = SkillDeleteSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        name = serializer.validated_data["name"]
        manager = SkillToolManager()
        result = manager.delete_user_tool(name, request.user)
        return _handle_crud_result(result, result.get("message", "Skill 已删除"))


class SkillUpdateView(APIView):
    """更新自定义 Skill 视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import SkillToolManager
        from Django_xm.apps.tools.serializers import SkillCreateSerializer

        serializer = SkillCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = SkillToolManager()
        result = manager.update_skill(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get("message", "Skill 更新成功"))


class SkillToggleView(APIView):
    """切换自定义 Skill 状态视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import SkillToolManager
        from Django_xm.apps.tools.serializers import SkillToggleSerializer

        serializer = SkillToggleSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="数据验证失败",
                code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        name = serializer.validated_data["name"]
        status = serializer.validated_data.get("status")
        manager = SkillToolManager()
        result = manager.toggle_user_tool(name, request.user, status)
        return _handle_crud_result(
            result,
            result.get("message", "状态切换成功"),
            data={"name": name, "status": result.get("status")},
        )


# ============================================================
# Skill 包管理（Agent Skills 规范）
# ============================================================


class SkillPackageListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.models import SkillPackage

        user_skills = SkillPackage.objects.filter(user=request.user).select_related("category")
        system_skills = SkillPackage.objects.filter(source="system").select_related("category")
        all_skills = user_skills | system_skills

        data = []
        for pkg in all_skills:
            data.append(
                {
                    "name": pkg.name,
                    "description": pkg.description,
                    "version": pkg.version,
                    "license": pkg.license,
                    "compatibility": pkg.compatibility,
                    "tool_type": pkg.tool_type,
                    "category": _build_user_tool_category_info(pkg)
                    if pkg.category
                    else {"code": "general", "name": "通用"},
                    "source": pkg.source,
                    "status": pkg.status,
                    "visibility": "selectable",
                    "tier": "extended",
                    "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
                    "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
                }
            )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(data, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return success_response(data=data)


class SkillPackageUploadView(APIView):
    """上传 Skill 包（ZIP 压缩包）"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.skills.loader import SkillLoader

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return error_response(message="请上传 ZIP 文件")

        if not uploaded_file.name.endswith(".zip"):
            return error_response(message="仅支持 .zip 格式的 Skill 包")

        # 保存临时文件
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            for chunk in uploaded_file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            loader = SkillLoader()
            success, message, metadata = loader.install_skill_package(tmp_path, request.user, source="user")
            if success:
                return success_response(data=metadata, message=message)
            return error_response(message=message)
        finally:
            import os

            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class SkillPackageDeleteView(APIView):
    """删除 Skill 包"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.skills.loader import SkillLoader

        name = request.data.get("name")
        if not name:
            return error_response(message="请提供 name 参数")

        loader = SkillLoader()
        success, message = loader.uninstall_skill_package(name, request.user)
        if success:
            return success_response(message=message)
        http_status = 403 if "系统级" in message else 400
        return error_response(message=message, http_status=http_status)


class SkillPackageToggleView(APIView):
    """启用/禁用 Skill 包"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.models import SkillPackage

        name = request.data.get("name")
        if not name:
            return error_response(message="请提供 name 参数")

        status = request.data.get("status")
        pkg = SkillPackage.objects.filter(user=request.user, name=name).first()
        if not pkg:
            return error_response(message=f"未找到 Skill 包 '{name}'")

        if status in ("active", "disabled"):
            pkg.status = status
        else:
            pkg.status = "disabled" if pkg.status == "active" else "active"

        pkg.save()
        return success_response(
            data={"name": name, "status": pkg.status}, message=f"Skill '{name}' 状态已更新为 {pkg.status}"
        )


class SkillPackageDetailView(APIView):
    """查看 Skill 包 SKILL.md 内容"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.skills.loader import SkillLoader

        name = request.query_params.get("name")
        if not name:
            return error_response(message="请提供 name 参数")

        loader = SkillLoader()
        content = loader.activate_skill(name, user_id=request.user.id)
        if content is None:
            return error_response(message=f"未找到 Skill '{name}' 或 SKILL.md 不存在")

        return success_response(
            data={
                "name": name,
                "instructions": content,
            }
        )


class ToolMetaView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.models import ToolCategory, UserToolResource

        categories = list(
            ToolCategory.objects.filter(is_active=True)
            .order_by("sort_order")
            .values(
                "code",
                "name",
                "icon",
            )
        )
        tool_types = [{"code": choice[0], "name": choice[1]} for choice in UserToolResource.ToolType.choices]
        sources = [{"code": choice[0], "name": choice[1]} for choice in UserToolResource.Source.choices]

        return success_response(
            data={
                "categories": categories,
                "tool_types": tool_types,
                "sources": sources,
            }
        )
