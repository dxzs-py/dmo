"""
命令、权限和工具视图

提供斜杠命令、权限管理、工具确认、费用查询、项目上下文等接口。
"""

import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.common.responses import success_response, error_response
from Django_xm.apps.common.error_codes import ErrorCode

from .models import ChatSession, ChatMessage

logger = logging.getLogger(__name__)


class ChatCommandsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService, CacheTTL

        cache_key = "chat:commands"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        from .services.slash_commands import get_all_commands
        commands = get_all_commands()
        result = {'commands': commands}
        CacheService.set(cache_key, result, CacheTTL.TOOL_LONG)
        return success_response(data=result)


class ChatCommandExecuteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        command = request.data.get('command', '')
        session_id = request.data.get('session_id')

        from .services.slash_commands import parse_command, execute_command

        parsed = parse_command(command)
        if not parsed:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='无效的命令格式',
            )

        command_name, args = parsed
        context = {
            "args": args,
            "user_id": request.user.id,
            "session_id": session_id,
        }

        if session_id:
            session = ChatSession.objects.filter(
                session_id=session_id,
                user=request.user,
                is_deleted=False,
            ).first()
            if session:
                messages = ChatMessage.objects.filter(session=session).order_by('created_at')
                context["session"] = {
                    "session_id": session.session_id,
                    "title": session.title,
                    "mode": session.mode,
                }
                context["messages"] = [
                    {"role": m.role, "content": m.content}
                    for m in messages
                ]

        result = execute_command(command_name, context)
        return success_response(data=result)


class ChatPermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.core.permissions import PermissionService
        session_id = request.query_params.get('session_id')
        info = PermissionService.get_permission_info(request.user.id, session_id)
        return success_response(data=info)

    def put(self, request):
        from Django_xm.apps.core.permissions import PermissionService
        session_mode = request.data.get('session_mode')
        tool_permissions = request.data.get('tool_permissions', {})
        session_id = request.data.get('session_id')

        if session_mode:
            try:
                policy = PermissionService.update_session_mode(
                    request.user.id, session_mode, session_id
                )
            except ValueError as e:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message=str(e),
                )

        tool_errors = []
        for tool_name, permission in tool_permissions.items():
            try:
                PermissionService.set_tool_permission(
                    request.user.id, tool_name, permission, session_id
                )
            except ValueError as e:
                tool_errors.append(f'{tool_name}: {str(e)}')

        if tool_errors:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f'部分工具权限设置失败: {"; ".join(tool_errors)}',
            )

        info = PermissionService.get_permission_info(request.user.id, session_id)
        return success_response(data=info, message='权限更新成功')


class ToolConfirmationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.core.permissions import get_pending_confirmation
        confirm_id = request.query_params.get('confirm_id')
        if not confirm_id:
            return error_response(code=ErrorCode.INVALID_PARAMS, message='缺少 confirm_id')

        entry = get_pending_confirmation(confirm_id)
        if not entry:
            return error_response(code=ErrorCode.NOT_FOUND, message='确认请求不存在或已过期')

        if entry['user_id'] != request.user.id:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无权操作此确认请求')

        return success_response(data={
            'confirm_id': confirm_id,
            'tool_name': entry['tool_name'],
            'tool_args': entry['tool_args'],
            'status': entry['status'],
        })

    def post(self, request):
        from Django_xm.apps.core.permissions import (
            get_pending_confirmation, approve_tool_confirmation, deny_tool_confirmation,
        )
        confirm_id = request.data.get('confirm_id')
        action = request.data.get('action', 'approve')

        if not confirm_id:
            return error_response(code=ErrorCode.INVALID_PARAMS, message='缺少 confirm_id')

        entry = get_pending_confirmation(confirm_id)
        if not entry:
            return error_response(code=ErrorCode.NOT_FOUND, message='确认请求不存在或已过期')

        if entry['user_id'] != request.user.id:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无权操作此确认请求')

        if action == 'approve':
            result = approve_tool_confirmation(confirm_id)
            if result is None:
                return error_response(code=ErrorCode.NOT_FOUND, message='确认请求不存在')
            return success_response(data={
                'confirm_id': confirm_id,
                'tool_name': entry['tool_name'],
                'status': 'executed',
                'result': result,
            }, message='工具已批准并执行')
        elif action == 'deny':
            success = deny_tool_confirmation(confirm_id)
            if not success:
                return error_response(code=ErrorCode.NOT_FOUND, message='确认请求不存在')
            return success_response(data={
                'confirm_id': confirm_id,
                'tool_name': entry['tool_name'],
                'status': 'denied',
            }, message='工具已拒绝')
        else:
            return error_response(code=ErrorCode.INVALID_PARAMS, message=f'无效的操作: {action}')


class ChatCostView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService, CacheTTL

        cache_key = "chat:cost_pricing"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        from Django_xm.apps.ai_engine.services.cost_tracker import get_all_model_pricing, MODEL_PRICING
        result = {
            'modelPricing': get_all_model_pricing(),
            'supportedModels': list(MODEL_PRICING.keys()),
        }
        CacheService.set(cache_key, result, CacheTTL.TOOL_LONG)
        return success_response(data=result)


class ProjectContextView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService, CacheTTL

        user = request.user
        cache_key = f"project_context:user_{user.id}"
        cached = CacheService.get(cache_key)
        if cached is not None:
            logger.info("项目上下文缓存命中")
            return success_response(data=cached)

        try:
            from Django_xm.apps.ai_engine.services.project_context import detect_project_context
            search_path = request.query_params.get('path')
            context = detect_project_context(search_path)
            result = context.to_dict()
            CacheService.set(cache_key, result, CacheTTL.QUERY_LONG)
            return success_response(data=result)
        except Exception as e:
            return error_response(
                code=ErrorCode.INTERNAL_ERROR,
                message=str(e),
            )
