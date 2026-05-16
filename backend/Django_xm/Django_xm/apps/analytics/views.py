import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.common.responses import success_response, error_response
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.common.request_utils import get_client_ip, get_user_agent

from .services.analytics_service import AnalyticsService
from .serializers import PageViewSerializer, FeatureUseSerializer, UserEventWriteSerializer

logger = logging.getLogger(__name__)


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            data = AnalyticsService.get_dashboard_stats(request.user)
            return success_response(data=data)
        except Exception as e:
            logger.error(f"获取仪表盘数据失败: {e}")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="获取仪表盘数据失败",
            )


class PageViewTrackView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
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
