import logging
import time
import json
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


class RequestTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request._start_time = time.time()
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        if hasattr(request, '_start_time'):
            duration = time.time() - request._start_time
            response['X-Request-Duration'] = f'{duration:.3f}'
            if duration > 30:
                logger.warning(
                    'Slow request: %s %s took %.2fs',
                    request.method,
                    request.get_full_path(),
                    duration
                )
        return response


class RequestLoggingMiddleware:
    """
    请求日志中间件

    记录 API 请求的方法、路径、状态码、耗时、用户信息
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/api/'):
            return self.get_response(request)

        request._log_start_time = time.time()
        request._log_request_id = id(request)

        user_info = 'anonymous'
        if hasattr(request, 'user') and request.user.is_authenticated:
            user_info = f'user:{request.user.id}'

        logger.info(
            f'[API] --> {request.method} {request.get_full_path()} ({user_info})'
        )

        response = self.get_response(request)

        duration = time.time() - request._log_start_time
        status_code = response.status_code

        log_level = logging.WARNING if status_code >= 400 else logging.INFO
        logger.log(
            log_level,
            f'[API] <-- {request.method} {request.get_full_path()} '
            f'{status_code} {duration:.3f}s ({user_info})'
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


class RateLimitMiddleware:
    """
    API 限流中间件

    基于 Redis 的滑动窗口限流策略
    """

    RATE_LIMITS = {
        '/api/chat/': (30, 60),
        '/api/research/': (5, 60),
        '/api/knowledge/': (60, 60),
        '/api/auth/': (10, 60),
    }

    DEFAULT_LIMIT = (120, 60)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/api/'):
            return self.get_response(request)

        if request.method == 'OPTIONS':
            return self.get_response(request)

        limit, window = self._get_limit_for_path(request.path)

        client_id = self._get_client_id(request)
        if client_id is None:
            return self.get_response(request)

        cache_key = f'ratelimit:{client_id}:{request.path}'

        try:
            current = cache.get(cache_key, 0)
            if isinstance(current, int) and current >= limit:
                logger.warning(
                    f'Rate limit exceeded: {client_id} -> {request.path} '
                    f'({current}/{limit} in {window}s)'
                )
                return JsonResponse(
                    {
                        'code': 429,
                        'message': '请求过于频繁，请稍后再试',
                        'error': 'RATE_LIMIT_EXCEEDED',
                        'retry_after': window,
                    },
                    status=429,
                )

            cache.set(cache_key, current + 1, timeout=window)
        except Exception as e:
            logger.error(f'限流检查失败: {e}')

        return self.get_response(request)

    def _get_limit_for_path(self, path: str):
        for prefix, limit_config in self.RATE_LIMITS.items():
            if path.startswith(prefix):
                return limit_config
        return self.DEFAULT_LIMIT

    def _get_client_id(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            return f'user:{request.user.id}'

        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', '')

        return f'ip:{ip}' if ip else None
