import logging
import time

from django.utils import timezone

from Django_xm.apps.analytics.models import EventCategory, EventType
from Django_xm.common.request_utils import get_client_ip

logger = logging.getLogger(__name__)

EXCLUDED_PATHS = (
    "/admin/",
    "/static/",
    "/media/",
    "/api/v1/health/",
    "/api/v1/monitor/",
    "/api/v1/schema/",
    "/api/v1/docs/",
    "/favicon.ico",
)


class AnalyticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_skip(request):
            return self.get_response(request)

        request._analytics_start = time.time()
        response = self.get_response(request)
        self._record_api_event(request, response)
        return response

    def _should_skip(self, request):
        path = request.path
        for excluded in EXCLUDED_PATHS:
            if path.startswith(excluded):
                return True
        if not path.startswith("/api/"):
            return True
        return request.method == "OPTIONS"

    def _record_api_event(self, request, response):
        """API 埋点分流。

        实测 api.request 曾占 UserEvent 全表 97.18%（217,481/223,782），且 Top 路径
        全是元数据/配置类 GET，分析价值极低。现改为：

        - **总是**把请求累加到 ApiDailyStat（仪表盘 api_requests / api_errors /
          平均耗时 / 错误率的唯一来源）；
        - **仅当**「非 GET 方法」或「请求失败」时额外写 UserEvent 明细，保留业务
          审计与失败单次追查能力；成功的 GET 不落明细。

        收敛规则按"HTTP 方法 + 成败"而非路径黑名单：本项目路径内嵌资源 ID，路径
        基数无界（单用户单日去重路径中位数 574、最大 12,393），路径黑名单不可维护。
        """
        try:
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated:
                return

            duration_ms = int((time.time() - request._analytics_start) * 1000)
            status_code = response.status_code
            is_success = 200 <= status_code < 400

            stat_data = {
                "user_id": user.id,
                "date": timezone.localdate(),
                "is_success": is_success,
                "duration_ms": duration_ms,
                "path": request.path,
            }

            detail_event = None
            if request.method != "GET" or not is_success:
                detail_event = self._build_detail_event(
                    request, user, status_code, is_success, duration_ms
                )

            from Django_xm.tasks.analytics_tasks import track_api_stat

            track_api_stat.delay(stat_data, detail_event)
        except Exception as e:
            logger.warning(f"Analytics中间件记录事件失败: {e}")

    def _build_detail_event(self, request, user, status_code, is_success, duration_ms):
        """构造 `api.request` 明细行载荷（仅非 GET 或失败请求调用）。"""
        event_category = self._infer_category(request.path)
        metadata = {
            "method": request.method,
            "path": request.path,
            "status_code": status_code,
        }

        if event_category == EventCategory.CHAT and "/stream/" in request.path:
            metadata["is_stream"] = True

        return {
            "user_id": user.id,
            "event_type": EventType.API_REQUEST,
            "event_category": event_category,
            "metadata": metadata,
            "ip_address": get_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
            "duration_ms": duration_ms,
            "is_success": is_success,
            "error_message": "" if is_success else f"HTTP {status_code}",
        }

    def _infer_category(self, path):
        if "/chat/" in path:
            return EventCategory.CHAT
        if "/knowledge/" in path or "/rag/" in path:
            return EventCategory.RAG
        if "/learning/" in path or "/workflow/" in path:
            return EventCategory.WORKFLOW
        if "/research/" in path:
            return EventCategory.RESEARCH
        if "/users/" in path:
            return EventCategory.AUTH
        return EventCategory.API_CALL
