"""
命令、费用和项目上下文视图

提供斜杠命令、费用查询、项目上下文等接口。
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.throttling import MetaRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response

from .models import ChatMessage

logger = logging.getLogger(__name__)


class ChatCommandsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService, CacheTTL

        cache_key = "chat:commands"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        from .services.slash_commands import get_all_commands

        commands = get_all_commands()
        result = {"commands": commands}
        CacheService.set(cache_key, result, CacheTTL.TOOL_LONG)
        return success_response(data=result)


class ChatCommandExecuteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def post(self, request):
        command = request.data.get("command", "")
        session_id = request.data.get("session_id")

        from .services.slash_commands import execute_command, parse_command

        parsed = parse_command(command)
        if not parsed:
            raise BaseAppError("无效的命令格式", business_code=ErrorCode.INVALID_PARAMS)

        command_name, args = parsed
        context = {
            "args": args,
            "user_id": request.user.id,
            "session_id": session_id,
        }

        if session_id:
            from Django_xm.apps.chat.services.message_service import get_user_session

            session = get_user_session(request.user, session_id)
            if session:
                messages = ChatMessage.objects.filter(session=session).order_by("created_at")
                context["session"] = {
                    "session_id": session.session_id,
                    "title": session.title,
                    "mode": session.mode,
                }
                context["messages"] = [{"role": m.role, "content": m.content} for m in messages]

        result = execute_command(command_name, context)
        return success_response(data=result)


class ProjectContextView(APIView):
    permission_classes = [IsAuthenticated]
    # 页面加载即请求的只读接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

    @extend_schema(exclude=True)
    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import CacheService, CacheTTL

        user = request.user
        cache_key = f"project_context:user_{user.id}"
        cached = CacheService.get(cache_key)
        if cached is not None:
            logger.debug("项目上下文缓存命中")
            return success_response(data=cached)

        from Django_xm.apps.ai_engine.services.project_context import detect_project_context

        search_path = request.query_params.get("path")
        context = detect_project_context(search_path)
        result = context.to_dict()
        CacheService.set(cache_key, result, CacheTTL.QUERY_LONG)
        return success_response(data=result)
