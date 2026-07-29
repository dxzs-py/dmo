import logging

from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware:
    """安全响应头中间件

    为所有响应添加安全相关的 HTTP 头，包括 XSS 防护、点击劫持防护、
    内容类型嗅探防护等。生产环境额外启用 HSTS 和 CSP。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["X-XSS-Protection"] = "1; mode=block"
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        if not settings.DEBUG:
            response["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self';"
            )

        return response


class SessionSecurityMiddleware:
    """会话安全中间件

    检测会话与用户身份不匹配的情况，防止会话固定攻击。
    当检测到会话归属用户与当前请求用户不一致时，强制登出并返回 403。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.process_request(request)
        if response is not None:
            return response
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_request(self, request):
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return None

        if not hasattr(request, "session") or not request.session.session_key:
            return None

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            return None

        user_id = request.user.id
        stored_session_user_id = request.session.get("user_id")

        if stored_session_user_id and str(stored_session_user_id) != str(user_id):
            logger.warning(
                f"Session mismatch detected! "
                f"Session belongs to user {stored_session_user_id}, "
                f"but request is from user {user_id}. "
                f"This could indicate a security issue."
            )

            logout(request)

            return JsonResponse(
                {"code": 403, "message": "会话验证失败，请重新登录", "error": "SESSION_MISMATCH"}, status=403
            )

        if not stored_session_user_id:
            request.session["user_id"] = user_id
            request.session.save()

        return None

    def process_response(self, request, response):
        return response
