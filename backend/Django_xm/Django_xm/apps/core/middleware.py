import logging
import time
from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.core.cache import cache
from django.utils import timezone
from Django_xm.apps.core.base_models import set_current_request, clear_current_request

logger = logging.getLogger(__name__)


class CurrentRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        try:
            response = self.get_response(request)
        finally:
            clear_current_request()
        return response


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['X-XSS-Protection'] = '1; mode=block'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'

        if not settings.DEBUG:
            response['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
            response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self';"

        return response


class SessionSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.process_request(request)
        if response is not None:
            return response
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_request(self, request):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return None

        if not hasattr(request, 'session') or not request.session.session_key:
            return None

        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            return None

        user_id = request.user.id
        stored_session_user_id = request.session.get('user_id')

        if stored_session_user_id and str(stored_session_user_id) != str(user_id):
            logger.warning(
                f"Session mismatch detected! "
                f"Session belongs to user {stored_session_user_id}, "
                f"but request is from user {user_id}. "
                f"This could indicate a security issue."
            )

            logout(request)

            return JsonResponse({
                'code': 403,
                'message': '会话验证失败，请重新登录',
                'error': 'SESSION_MISMATCH'
            }, status=403)

        if not stored_session_user_id:
            request.session['user_id'] = user_id
            request.session.save()

        return None

    def process_response(self, request, response):
        return response


class APIRequestMiddleware:
    """
    API 请求中间件

    合并原 RequestTimeoutMiddleware 和 RequestLoggingMiddleware 的职责：
    1. 记录请求耗时（X-Request-Duration 头）
    2. 记录 API 请求日志（方法、路径、状态码、耗时、用户）
    3. 慢请求告警（>30s）
    """

    SLOW_REQUEST_THRESHOLD = 30

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/api/'):
            request._start_time = time.time()
            response = self.get_response(request)
            if hasattr(request, '_start_time'):
                duration = time.time() - request._start_time
                response['X-Request-Duration'] = f'{duration:.3f}'
            return response

        request._start_time = time.time()

        user_info = 'anonymous'
        if hasattr(request, 'user') and request.user.is_authenticated:
            user_info = f'user:{request.user.id}'

        logger.info(
            f'[API] --> {request.method} {request.get_full_path()} ({user_info})'
        )

        response = self.get_response(request)

        duration = time.time() - request._start_time
        status_code = response.status_code

        response['X-Request-Duration'] = f'{duration:.3f}'

        log_level = logging.WARNING if status_code >= 400 else logging.INFO
        logger.log(
            log_level,
            f'[API] <-- {request.method} {request.get_full_path()} '
            f'{status_code} {duration:.3f}s ({user_info})'
        )

        if duration > self.SLOW_REQUEST_THRESHOLD:
            logger.warning(
                'Slow request: %s %s took %.2fs',
                request.method,
                request.get_full_path(),
                duration
            )

        return response


class CacheControlMiddleware:
    """
    缓存控制中间件

    为 API 响应添加缓存控制头
    """

    CACHE_POLICIES = {
        '/api/chat/': 'no-store, no-cache, must-revalidate',
        '/api/research/': 'no-store, no-cache, must-revalidate',
        '/api/knowledge/': 'private, max-age=300',
        '/api/core/cache/': 'no-store, no-cache, must-revalidate',
    }

    DEFAULT_POLICY = 'private, max-age=60'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not request.path.startswith('/api/'):
            return response

        if 'Cache-Control' in response:
            return response

        policy = self.DEFAULT_POLICY
        for path_prefix, cache_policy in self.CACHE_POLICIES.items():
            if request.path.startswith(path_prefix):
                policy = cache_policy
                break

        response['Cache-Control'] = policy
        response['X-Cache-Policy'] = 'middleware'
        return response


class AIExceptionMiddleware:
    """
    AI 服务全局异常处理中间件

    捕获 LangChain/OpenAI/Anthropic 等AI服务异常，
    返回统一格式的 JSON 响应，避免泄露内部错误信息。
    """

    EXCEPTION_MAP = None

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    @classmethod
    def _build_exception_map(cls):
        if cls.EXCEPTION_MAP is not None:
            return cls.EXCEPTION_MAP

        mapping = {}

        try:
            from openai import (
                APIError,
                APIConnectionError,
                RateLimitError,
                AuthenticationError,
                BadRequestError,
                APITimeoutError,
            )
            mapping[RateLimitError] = (429, "AI 服务请求频率超限，请稍后重试", "AI_RATE_LIMIT")
            mapping[AuthenticationError] = (401, "AI 服务认证失败", "AI_AUTH_ERROR")
            mapping[BadRequestError] = (400, "AI 请求参数错误", "AI_BAD_REQUEST")
            mapping[APITimeoutError] = (504, "AI 服务响应超时", "AI_TIMEOUT")
            mapping[APIConnectionError] = (502, "AI 服务连接失败", "AI_CONNECTION_ERROR")
            mapping[APIError] = (502, "AI 服务暂时不可用", "AI_SERVICE_ERROR")
        except ImportError:
            pass

        try:
            from langchain_core.exceptions import OutputParserException
            mapping[OutputParserException] = (422, "AI 输出解析失败", "AI_OUTPUT_PARSE_ERROR")
        except ImportError:
            pass

        try:
            from langgraph.errors import GraphRecursionError
            mapping[GraphRecursionError] = (422, "Agent 执行超出最大迭代次数", "AI_RECURSION_LIMIT")
        except ImportError:
            pass

        try:
            from httpx import TimeoutException, ConnectError
            mapping[TimeoutException] = (504, "AI 服务响应超时", "AI_TIMEOUT")
            mapping[ConnectError] = (502, "AI 服务连接失败", "AI_CONNECTION_ERROR")
        except ImportError:
            pass

        cls.EXCEPTION_MAP = mapping
        return mapping

    def process_exception(self, request, exception):
        if not request.path.startswith('/api/'):
            return None

        exception_map = self._build_exception_map()

        for exc_class, (status_code, message, error_code) in exception_map.items():
            if isinstance(exception, exc_class):
                logger.error(
                    f"[AI Exception] {error_code}: {type(exception).__name__} - {exception}",
                    exc_info=True,
                )
                return JsonResponse(
                    {
                        "code": status_code,
                        "message": message,
                        "error": error_code,
                    },
                    status=status_code,
                )

        error_name = type(exception).__name__
        ai_related = any(
            keyword in error_name.lower()
            for keyword in ["langchain", "langgraph", "openai", "anthropic", "agent", "llm", "embedding"]
        )

        if ai_related:
            logger.error(
                f"[AI Exception] Unhandled: {error_name} - {exception}",
                exc_info=True,
            )
            return JsonResponse(
                {
                    "code": 500,
                    "message": "AI 服务内部错误，请稍后重试",
                    "error": "AI_INTERNAL_ERROR",
                },
                status=500,
            )

        return None

