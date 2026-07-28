import logging
import time

from Django_xm.apps.core.base_models import clear_current_request, set_current_request

logger = logging.getLogger(__name__)


class CurrentRequestMiddleware:
    """当前请求中间件

    将当前请求对象存储到线程局部变量中，便于在模型层等位置获取请求上下文。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        try:
            response = self.get_response(request)
        finally:
            clear_current_request()
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
