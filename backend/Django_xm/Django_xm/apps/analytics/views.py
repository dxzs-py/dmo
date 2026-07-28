"""Analytics 视图

异常处理策略（与全局 custom_exception_handler 协同）：
- ValidationError 由 serializer.is_valid(raise_exception=True) 抛出 → 冒泡到全局 handler 返回 400
- 其他未预期异常（数据库故障 / Redis 不可达等）→ 冒泡到全局 handler 返回 500
- 本地仅捕获"业务上可恢复"的特定异常，避免 broad except 吞掉真实故障信号
"""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.common.request_utils import get_client_ip, get_user_agent
from Django_xm.common.responses import success_response

from .serializers import FeatureUseSerializer, PageViewSerializer, UserEventWriteSerializer
from .services.analytics_service import AnalyticsService

logger = logging.getLogger(__name__)


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(view=False)
    def get(self, request):
        """获取仪表盘统计数据

        AnalyticsService.get_dashboard_stats 内部已做缓存 + 异常隔离，
        未预期异常冒泡到全局 handler 返回 500。
        """
        data = AnalyticsService.get_dashboard_stats(request.user)
        return success_response(data=data)


class PageViewTrackView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """记录页面浏览事件

        - ValidationError → 全局 handler 返回 400
        - AnalyticsService.record_page_view 内部失败 → 冒泡到全局 handler
        """
        serializer = PageViewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        AnalyticsService.record_page_view(
            user=request.user,
            page_path=serializer.validated_data['path'],
            page_title=serializer.validated_data.get('title', ''),
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request, max_length=None),
        )
        return success_response(message="页面浏览已记录")


class FeatureUseTrackView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """记录功能使用事件

        - ValidationError → 全局 handler 返回 400
        - AnalyticsService.record_feature_usage 内部失败 → 冒泡到全局 handler
        """
        serializer = FeatureUseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        AnalyticsService.record_feature_usage(
            user=request.user,
            feature_name=serializer.validated_data['feature'],
            metadata=serializer.validated_data.get('metadata', {}),
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request, max_length=None),
        )
        return success_response(message="功能使用已记录")


class EventTrackView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """记录用户事件

        - ValidationError → 全局 handler 返回 400
        - UserEvent.objects.create / cache invalidate 失败 → 冒泡到全局 handler
        """
        serializer = UserEventWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from Django_xm.apps.analytics.models import UserEvent
        UserEvent.objects.create(
            user=request.user,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
            **serializer.validated_data,
        )
        AnalyticsService.invalidate_user_cache(request.user.id)
        return success_response(message="事件已记录")
