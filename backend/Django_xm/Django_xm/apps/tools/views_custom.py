import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination

from Django_xm.common.responses import success_response, error_response
from Django_xm.common.error_codes import ErrorCode
from Django_xm.apps.tools.managers import _build_user_tool_category_info

logger = logging.getLogger(__name__)

ERROR_CODE_TO_STATUS = {'FORBIDDEN': 403, 'NOT_FOUND': 404, 'VALIDATION': 400}


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        if 'page' not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view)


def _handle_crud_result(result, success_msg="操作成功", data=None):
    """统一处理 Manager 返回结果"""
    if result.get('success'):
        response_data = data if data is not None else result.get('data')
        return success_response(message=success_msg, data=response_data)
    error_code = result.get('error_code', 'UNKNOWN')
    http_status = ERROR_CODE_TO_STATUS.get(error_code, 400)
    return error_response(message=result.get('message', '操作失败'), http_status=http_status)


class ToolListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.tools.managers import (
            LangChainToolManager, McpToolManager, SkillToolManager,
        )

        tool_type = request.query_params.get('type', '').strip()
        user = request.user

        managers = {
            'langchain': LangChainToolManager(),
            'mcp': McpToolManager(),
            'skill': SkillToolManager(),
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
                return success_response(data={tool_type: tools, 'total': len(tools)})
            except Exception as e:
                logger.error(f"获取 {tool_type} 工具列表失败: {e}")
                return error_response(message=f"获取工具列表失败: {str(e)}")

        result = {}
        total = 0
        for t, manager in managers.items():
            try:
                tools = manager.get_tool_info_list(user)
                result[t] = tools
                total += len(tools)
            except Exception as e:
                logger.error(f"获取 {t} 工具列表失败: {e}")
                result[t] = []

        all_tools = []
        for tools in result.values():
            all_tools.extend(tools)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(all_tools, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return success_response(data={**result, 'total': total})


class ToolUploadView(APIView):
    """自定义工具上传视图

    用户上传的 @tool 装饰器代码保存到数据库，默认激活。
    运行时通过受限沙箱动态加载为 BaseTool 实例。
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.serializers import McpToolUploadSerializer
        from Django_xm.apps.tools.models import CustomTool, ToolCategory

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
            category = ToolCategory.objects.get(code='general')

        try:
            tool_obj = CustomTool.objects.create(
                user=request.user,
                name=tool_name,
                description=description,
                code=tool_code,
                tool_type='langchain',
                category=category,
                source='user',
                status='active',
            )
            logger.info(f"用户上传自定义工具: {tool_name} (user={request.user.id})")
            return success_response(data={
                "id": tool_obj.id,
                "name": tool_obj.name,
                "description": tool_obj.description,
                "status": tool_obj.status,
            }, message=f"工具 '{tool_name}' 上传成功")
        except Exception as e:
            logger.error(f"工具上传失败: {e}")
            return error_response(message=f"工具上传失败: {str(e)}")


class CustomToolListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.tools.models import CustomTool

        tools = CustomTool.objects.filter(user=request.user).select_related('category')
        data = [{
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
        } for t in tools]
        paginator = StandardPagination()
        page = paginator.paginate_queryset(data, request)
        if page is not None:
            return paginator.get_paginated_response(page)
        return success_response(data={"tools": data, "total": len(data)})


class CustomToolDeleteView(APIView):
    """删除自定义工具"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.managers import LangChainToolManager

        tool_name = request.data.get("name")
        if not tool_name:
            return error_response(message="请提供 name 参数")

        manager = LangChainToolManager()
        result = manager.delete_user_tool(tool_name, request.user)
        return _handle_crud_result(result, result.get('message', '自定义工具已删除'))


class CustomToolToggleView(APIView):
    """启用/禁用自定义工具"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Django_xm.apps.tools.managers import LangChainToolManager

        tool_name = request.data.get("name")
        if not tool_name:
            return error_response(message="请提供 name 参数")

        status = request.data.get("status")
        manager = LangChainToolManager()
        result = manager.toggle_user_tool(tool_name, request.user, status)
        return _handle_crud_result(
            result, result.get('message', '状态切换成功'),
            data={"name": tool_name, "status": result.get('status')},
        )


class CustomToolUpdateView(APIView):
    permission_classes = [IsAuthenticated]

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
        return success_response(data={
            "name": tool_obj.name,
            "description": tool_obj.description,
            "status": tool_obj.status,
        }, message=f"工具 '{tool_name}' 更新成功")


class ToolMetaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.tools.models import ToolCategory, UserToolResource

        categories = list(
            ToolCategory.objects.filter(is_active=True).order_by('sort_order').values(
                'code', 'name', 'icon',
            )
        )
        tool_types = [
            {'code': choice[0], 'name': choice[1]}
            for choice in UserToolResource.ToolType.choices
        ]
        sources = [
            {'code': choice[0], 'name': choice[1]}
            for choice in UserToolResource.Source.choices
        ]

        return success_response(data={
            'categories': categories,
            'tool_types': tool_types,
            'sources': sources,
        })
