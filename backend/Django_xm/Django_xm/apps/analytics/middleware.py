import logging
import time
from django.conf import settings
from Django_xm.apps.analytics.models import UserEvent, EventCategory, EventType
from Django_xm.apps.common.request_utils import get_client_ip

logger = logging.getLogger(__name__)

EXCLUDED_PATHS = (
    '/admin/',
    '/static/',
    '/media/',
    '/api/v1/health/',
    '/api/v1/monitor/',
    '/api/v1/schema/',
    '/api/v1/docs/',
    '/favicon.ico',
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
        if not path.startswith('/api/'):
            return True
        if request.method == 'OPTIONS':
            return True
        return False

    def _record_api_event(self, request, response):
        try:
            user = getattr(request, 'user', None)
            if not user or not user.is_authenticated:
                return

            duration_ms = int((time.time() - request._analytics_start) * 1000)
            status_code = response.status_code
            is_success = 200 <= status_code < 400

            event_category = self._infer_category(request.path)
            event_type = EventType.API_REQUEST

            metadata = {
                'method': request.method,
                'path': request.path,
                'status_code': status_code,
            }

            if event_category == EventCategory.CHAT and '/stream/' in request.path:
                metadata['is_stream'] = True

            UserEvent.objects.create(
                user=user,
                event_type=event_type,
                event_category=event_category,
                metadata=metadata,
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                duration_ms=duration_ms,
                is_success=is_success,
                error_message='' if is_success else f'HTTP {status_code}',
            )
        except Exception as e:
            logger.warning(f"Analytics中间件记录事件失败: {e}")

    def _infer_category(self, path):
        if '/chat/' in path:
            return EventCategory.CHAT
        if '/knowledge/' in path or '/rag/' in path:
            return EventCategory.RAG
        if '/learning/' in path or '/workflow/' in path:
            return EventCategory.WORKFLOW
        if '/research/' in path:
            return EventCategory.RESEARCH
        if '/users/' in path:
            return EventCategory.AUTH
        return EventCategory.API_CALL
