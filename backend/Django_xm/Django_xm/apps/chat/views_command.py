"""
命令和项目上下文视图

提供斜杠命令列表、项目上下文等接口。
（命令执行 REST 通道 /chat/commands/execute/ 已随 WS 化改造下线，
前端斜杠命令由 useChatCommands 客户端处理器承载。）
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.throttling import MetaRateThrottle
from Django_xm.common.responses import success_response

logger = logging.getLogger(__name__)


class ChatCommandsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import (
            CACHE_PREFIX_CHAT,
            CacheService,
            CacheTTL,
        )

        cache_key = f"{CACHE_PREFIX_CHAT}:commands"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        from .services.slash_commands import get_all_commands

        commands = get_all_commands()
        result = {"commands": commands}
        CacheService.set(cache_key, result, CacheTTL.CONFIG_LONG)
        return success_response(data=result)


class ProjectContextView(APIView):
    permission_classes = [IsAuthenticated]
    # 页面加载即请求的只读接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

    @extend_schema(exclude=True)
    def get(self, request):
        from Django_xm.apps.cache_manager.services.cache_service import (
            CACHE_PREFIX_PROJECT_CONTEXT,
            CacheService,
            CacheTTL,
        )

        user = request.user
        cache_key = f"{CACHE_PREFIX_PROJECT_CONTEXT}:user_{user.id}"
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
