"""
命令和项目上下文视图

提供斜杠命令列表、项目上下文等接口。
（命令执行 REST 通道 /chat/commands/execute/ 已随 WS 化改造下线，
前端斜杠命令由 useChatCommands 客户端处理器承载。）
"""

import hashlib
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
        search_path = request.query_params.get("path")

        # 缓存键**必须**包含 search_path：detect_project_context 以它为根目录
        # （project_root / project_name / git 分支 / 语言框架 / 目录结构 / 关键文件
        # 全部随 path 变化）。此前只按 user 分键，导致同一用户先请求 ?path=/a
        # 再请求 ?path=/b 时，24h（QUERY_LONG）内都会拿到 /a 的结果——跨项目串数据。
        # 哈希策略沿用 cache_service 的约定：SHA256 前 24 位。
        path_hash = hashlib.sha256((search_path or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
        cache_key = f"{CACHE_PREFIX_PROJECT_CONTEXT}:user_{user.id}:path_{path_hash}"

        cached = CacheService.get(cache_key)
        if cached is not None:
            logger.debug("项目上下文缓存命中")
            return success_response(data=cached)

        from Django_xm.apps.ai_engine.services.project_context import detect_project_context

        context = detect_project_context(search_path)
        result = context.to_dict()
        CacheService.set(cache_key, result, CacheTTL.QUERY_LONG)
        return success_response(data=result)
