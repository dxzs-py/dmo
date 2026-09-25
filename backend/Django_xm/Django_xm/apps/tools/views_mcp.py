import logging

from asgiref.sync import sync_to_async
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.throttling import MetaRateThrottle, SensitiveOperationRateThrottle
from Django_xm.apps.tools.managers import _build_user_tool_category_info
from Django_xm.apps.tools.views_common import (
    StandardPagination,
    _handle_crud_result,
)
from Django_xm.async_utils import run_async
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response
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
            from Django_xm.apps.tools.mcp import is_mcp_available  # noqa: F401  # 仅用于导入可用性检测
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
    """MCP Server 连接测试（资源动作）

    仅能测试已保存且 status=active 的 Server（含系统级配置），
    name 经 URL path 传递。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request, name):
        servers = _get_merged_mcp_servers(user=request.user)
        target = None
        for srv in servers:
            if srv.get("name") == name:
                target = srv
                break

        if not target:
            raise NotFound(f"未找到 MCP Server: {name}")

        try:
            from Django_xm.apps.tools.mcp import get_mcp_tools  # noqa: F401  # 仅用于导入可用性检测
        except ImportError as e:
            raise BaseAppError("langchain-mcp-adapters 未安装", business_code=ErrorCode.SERVICE_UNAVAILABLE) from e

        data = run_async(_test_mcp_server(name, target))
        return success_response(data=data)


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
                # 配置校验异常：保持在此处抛出，由下方 except 统一转为错误响应（与缺失 url 场景的 API 契约一致）
                raise ValueError(f"MCP Server '{server_name}' 缺少 url 配置")  # noqa: TRY301
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
    except Exception:
        logger.exception(f"MCP Server 连接测试失败 ({server_name})")
        return {
            "server": server_name,
            "transport": transport,
            "connected": False,
            "error": "连接失败，详细信息请查看服务端日志",
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


class MethodThrottleMixin:
    """按 HTTP 方法区分限流类的 mixin。

    集合视图合并原"列表 GET 视图 + 写动词 POST 视图"后，两类方法的限流
    策略不同（如 GET=MetaRateThrottle、POST=SensitiveOperationRateThrottle）。
    ``throttle_classes_by_method`` 未覆盖的方法回退到 ``APIView.get_throttles``
    默认实现（类级 ``throttle_classes``，未显式声明时继承全局
    ``DEFAULT_THROTTLE_CLASSES``），与合并前各独立视图的限流行为一致。
    """

    throttle_classes_by_method: dict[str, list] = {}

    def get_throttles(self):
        throttle_classes = self.throttle_classes_by_method.get(self.request.method)
        if throttle_classes is None:
            return super().get_throttles()
        return [throttle() for throttle in throttle_classes]


# ---------------------------------------------------------------------------
# McpServer 资源（GET/POST /mcp/servers/、PUT/DELETE /mcp/servers/{name}/、
# PATCH /mcp/servers/{name}/status/）
# ---------------------------------------------------------------------------


class McpServerView(MethodThrottleMixin, APIView):
    """MCP Server 集合视图：GET 列表 / POST 添加用户级 Server"""

    permission_classes = [IsAuthenticated]
    # GET 列表保留独立 meta 额度（页面加载即请求的只读接口）；POST 沿用全局默认限流
    throttle_classes_by_method = {"GET": [MetaRateThrottle]}

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.managers import McpToolManager

        manager = McpToolManager()
        tools = manager.get_tool_info_list(request.user)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(tools, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return success_response(data={"servers": tools, "total": len(tools)})

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import McpToolManager
        from Django_xm.apps.tools.serializers import McpServerAddSerializer

        serializer = McpServerAddSerializer(data=request.data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = McpToolManager()
        result = manager.add_server(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get("message", "MCP Server 添加成功"))


class McpServerDetailView(APIView):
    """MCP Server 资源视图：PUT 更新 / DELETE 删除（name 经 URL path 传递）"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def put(self, request, name):
        from Django_xm.apps.tools.managers import McpToolManager
        from Django_xm.apps.tools.serializers import McpServerAddSerializer

        # name 以 path 为准（body 携带 name 时忽略），保持资源标识单一来源
        data = {**request.data, "name": name}
        serializer = McpServerAddSerializer(data=data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = McpToolManager()
        result = manager.update_server(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get("message", "MCP Server 更新成功"))

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def delete(self, request, name):
        from Django_xm.apps.tools.managers import McpToolManager

        manager = McpToolManager()
        result = manager.delete_user_tool(name, request.user)
        return _handle_crud_result(result, result.get("message", "MCP Server 已删除"))


class McpServerStatusView(APIView):
    """MCP Server 状态视图：PATCH 启用/禁用（body 可选 status，缺省自动取反）"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def patch(self, request, name):
        from Django_xm.apps.tools.managers import McpToolManager

        status = request.data.get("status")
        manager = McpToolManager()
        result = manager.toggle_user_tool(name, request.user, status)
        return _handle_crud_result(
            result,
            result.get("message", "状态切换成功"),
            data={"name": name, "status": result.get("status")},
        )


class McpServerDiscoverView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.serializers import McpServerDiscoverSerializer

        serializer = McpServerDiscoverSerializer(data=request.data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )
        registry_url = serializer.validated_data["registry_url"]

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


class ToolListView(APIView):
    permission_classes = [IsAuthenticated]
    # 页面加载即请求的只读接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

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
                raise BaseAppError(
                    f"无效的工具类型 '{tool_type}'，可选值: langchain, mcp, skill",
                    business_code=ErrorCode.VALIDATION_FAILED,
                )
            tools = managers[tool_type].get_tool_info_list(user)
            paginator = StandardPagination()
            page = paginator.paginate_queryset(tools, request)
            if page is not None:
                return paginator.get_paginated_response(page)
            return success_response(data={tool_type: tools, "total": len(tools)})

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


# ---------------------------------------------------------------------------
# CustomTool 资源（GET/POST /custom/、PUT/DELETE /custom/{name}/、
# PATCH /custom/{name}/status/）
# ---------------------------------------------------------------------------


class CustomToolView(MethodThrottleMixin, APIView):
    """自定义工具集合视图：GET 列表 / POST 上传创建

    用户上传的 @tool 装饰器代码保存到数据库，默认审核状态为 pending
    （等待管理员审核通过后才生效，approval_status='approved'）。
    运行时通过受限沙箱动态加载为 BaseTool 实例。

    限流：POST 上传保留 SensitiveOperationRateThrottle（scope='sensitive'）
    防止恶意刷上传；GET 列表沿用全局默认限流。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes_by_method = {"POST": [SensitiveOperationRateThrottle]}

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

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.models import CustomTool, ToolCategory
        from Django_xm.apps.tools.serializers import McpToolUploadSerializer

        serializer = McpToolUploadSerializer(data=request.data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )
        data = serializer.validated_data
        tool_name = data["name"]
        tool_code = data["code"]
        description = data.get("description", "")
        category_code = data.get("category", "general")

        if "@tool" not in tool_code:
            raise BaseAppError("代码必须包含 @tool 装饰器定义的 LangChain 工具", business_code=ErrorCode.INVALID_PARAMS)

        if CustomTool.objects.filter(user=request.user, name=tool_name).exists():
            raise BaseAppError(f"工具 '{tool_name}' 已存在", business_code=ErrorCode.DUPLICATE_RESOURCE)

        try:
            compile(tool_code, f"<tool:{tool_name}>", "exec")
        except SyntaxError as e:
            raise BaseAppError(f"代码语法错误: {e.msg} (行 {e.lineno})", business_code=ErrorCode.INVALID_PARAMS) from e

        try:
            category = ToolCategory.objects.get(code=category_code)
        except ToolCategory.DoesNotExist:
            category = ToolCategory.objects.get(code="general")

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


class CustomToolDetailView(APIView):
    """自定义工具资源视图：PUT 更新 / DELETE 删除（name 经 URL path 传递）"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def put(self, request, name):
        from Django_xm.apps.tools.models import CustomTool, ToolCategory

        tool_obj = CustomTool.objects.filter(user=request.user, name=name).first()
        if not tool_obj:
            raise NotFound(f"未找到自定义工具 '{name}'")

        new_code = request.data.get("code")
        new_desc = request.data.get("description")
        new_category = request.data.get("category")

        if new_code is not None:
            if "@tool" not in new_code:
                raise BaseAppError(
                    "代码必须包含 @tool 装饰器定义的 LangChain 工具",
                    business_code=ErrorCode.INVALID_PARAMS,
                )
            try:
                compile(new_code, f"<tool:{name}>", "exec")
            except SyntaxError as e:
                raise BaseAppError(
                    f"代码语法错误: {e.msg} (行 {e.lineno})",
                    business_code=ErrorCode.INVALID_PARAMS,
                ) from e
            tool_obj.code = new_code

        if new_desc is not None:
            tool_obj.description = new_desc

        if new_category is not None:
            try:
                tool_obj.category = ToolCategory.objects.get(code=new_category)
            except ToolCategory.DoesNotExist:
                pass

        tool_obj.save()
        logger.info(f"自定义工具 '{name}' 已更新 (user={request.user.id})")
        return success_response(
            data={
                "name": tool_obj.name,
                "description": tool_obj.description,
                "status": tool_obj.status,
            },
            message=f"工具 '{name}' 更新成功",
        )

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def delete(self, request, name):
        from Django_xm.apps.tools.managers import LangChainToolManager

        manager = LangChainToolManager()
        result = manager.delete_user_tool(name, request.user)
        return _handle_crud_result(result, result.get("message", "自定义工具已删除"))


class CustomToolStatusView(APIView):
    """自定义工具状态视图：PATCH 启用/禁用（body 可选 status，缺省自动取反）"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def patch(self, request, name):
        from Django_xm.apps.tools.managers import LangChainToolManager

        status = request.data.get("status")
        manager = LangChainToolManager()
        result = manager.toggle_user_tool(name, request.user, status)
        return _handle_crud_result(
            result,
            result.get("message", "状态切换成功"),
            data={"name": name, "status": result.get("status")},
        )


# ---------------------------------------------------------------------------
# Skill 资源（GET/POST /skills/、PUT/DELETE /skills/{name}/、
# PATCH /skills/{name}/status/）
# ---------------------------------------------------------------------------


class SkillView(MethodThrottleMixin, APIView):
    """Skill 集合视图：GET 列表 / POST 创建自定义 Skill"""

    permission_classes = [IsAuthenticated]
    # GET 列表保留独立 meta 额度（页面加载即请求的只读接口）；POST 沿用全局默认限流
    throttle_classes_by_method = {"GET": [MetaRateThrottle]}

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        from Django_xm.apps.tools.managers import SkillToolManager

        manager = SkillToolManager()
        skills = manager.get_tool_info_list(request.user)
        return success_response(data={"skills": skills, "total": len(skills)})

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.managers import SkillToolManager
        from Django_xm.apps.tools.serializers import SkillCreateSerializer

        serializer = SkillCreateSerializer(data=request.data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = SkillToolManager()
        result = manager.create_skill(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get("message", "Skill 创建成功"))


class SkillDetailView(APIView):
    """Skill 资源视图：PUT 更新 / DELETE 删除（name 经 URL path 传递）"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def put(self, request, name):
        from Django_xm.apps.tools.managers import SkillToolManager
        from Django_xm.apps.tools.serializers import SkillCreateSerializer

        # name 以 path 为准（body 携带 name 时忽略），保持资源标识单一来源
        data = {**request.data, "name": name}
        serializer = SkillCreateSerializer(data=data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

        manager = SkillToolManager()
        result = manager.update_skill(request.user, serializer.validated_data)
        return _handle_crud_result(result, result.get("message", "Skill 更新成功"))

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def delete(self, request, name):
        from Django_xm.apps.tools.managers import SkillToolManager

        manager = SkillToolManager()
        result = manager.delete_user_tool(name, request.user)
        return _handle_crud_result(result, result.get("message", "Skill 已删除"))


class SkillStatusView(APIView):
    """Skill 状态视图：PATCH 启用/禁用（body 可选 status，缺省自动取反）"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def patch(self, request, name):
        from Django_xm.apps.tools.managers import SkillToolManager
        from Django_xm.apps.tools.serializers import SkillToggleSerializer

        data = {"name": name}
        if "status" in request.data:
            data["status"] = request.data["status"]
        serializer = SkillToggleSerializer(data=data)
        if not serializer.is_valid():
            raise BaseAppError(
                "数据验证失败",
                business_code=ErrorCode.VALIDATION_FAILED,
                data={"details": serializer.errors},
            )

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
# GET/POST /skills/packages/、GET/DELETE /skills/packages/{name}/、
# PATCH /skills/packages/{name}/status/
# ============================================================


class SkillPackageView(MethodThrottleMixin, APIView):
    """Skill 包集合视图：GET 列表 / POST 上传 ZIP 包"""

    permission_classes = [IsAuthenticated]
    # GET 列表保留独立 meta 额度（页面加载即请求的只读接口）；POST 沿用全局默认限流
    throttle_classes_by_method = {"GET": [MetaRateThrottle]}

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

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request):
        from Django_xm.apps.tools.serializers import (
            RESOURCE_NAME_ERROR_MESSAGE,
            RESOURCE_NAME_PATTERN,
        )
        from Django_xm.apps.tools.skills.loader import SkillLoader

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            raise BaseAppError("请上传 ZIP 文件", business_code=ErrorCode.INVALID_PARAMS)

        if not uploaded_file.name.endswith(".zip"):
            raise BaseAppError("仅支持 .zip 格式的 Skill 包", business_code=ErrorCode.INVALID_PARAMS)

        # 保存临时文件
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            for chunk in uploaded_file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            loader = SkillLoader()
            # name 将作为资源标识符进入 URL path（删除/切换/详情端点），
            # 上传入口前置校验字符集，拒绝 "/" 等路径不安全字符
            is_valid, _, frontmatter = loader.validate_skill_package(tmp_path)
            if is_valid and frontmatter:
                pkg_name = frontmatter.get("name", "")
                if not RESOURCE_NAME_PATTERN.match(pkg_name):
                    raise BaseAppError(RESOURCE_NAME_ERROR_MESSAGE, business_code=ErrorCode.VALIDATION_FAILED)

            success, message, metadata = loader.install_skill_package(tmp_path, request.user, source="user")
            if success:
                return success_response(data=metadata, message=message)
            raise BaseAppError(message, business_code=ErrorCode.INVALID_PARAMS)
        finally:
            import os

            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class SkillPackageDetailView(APIView):
    """Skill 包资源视图：GET 查看 SKILL.md 内容 / DELETE 卸载（name 经 URL path 传递）"""

    permission_classes = [IsAuthenticated]

    # 显式 operation_id：避免与集合视图 SkillPackageView 的自动生成 id 冲突（W001）
    @extend_schema(operation_id="tools_skill_package_detail", responses={200: EmptySerializer})
    def get(self, request, name):
        from Django_xm.apps.tools.skills.loader import SkillLoader

        loader = SkillLoader()
        content = loader.activate_skill(name, user_id=request.user.id)
        if content is None:
            raise NotFound(f"未找到 Skill '{name}' 或 SKILL.md 不存在")

        return success_response(
            data={
                "name": name,
                "instructions": content,
            }
        )

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def delete(self, request, name):
        from Django_xm.apps.tools.skills.loader import SkillLoader

        loader = SkillLoader()
        success, message = loader.uninstall_skill_package(name, request.user)
        if success:
            return success_response(message=message)
        if "系统级" in message:
            raise PermissionDenied(message)
        raise BaseAppError(message, business_code=ErrorCode.INVALID_PARAMS)


class SkillPackageStatusView(APIView):
    """Skill 包状态视图：PATCH 启用/禁用（body 可选 status，缺省自动取反）"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def patch(self, request, name):
        from Django_xm.apps.tools.models import SkillPackage

        status = request.data.get("status")
        pkg = SkillPackage.objects.filter(user=request.user, name=name).first()
        if not pkg:
            raise NotFound(f"未找到 Skill 包 '{name}'")

        if status in ("active", "disabled"):
            pkg.status = status
        else:
            pkg.status = "disabled" if pkg.status == "active" else "active"

        pkg.save()
        return success_response(
            data={"name": name, "status": pkg.status}, message=f"Skill '{name}' 状态已更新为 {pkg.status}"
        )


class ToolMetaView(APIView):
    permission_classes = [IsAuthenticated]
    # 页面加载即请求的只读接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

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
