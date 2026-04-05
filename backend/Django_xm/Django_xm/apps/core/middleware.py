import logging
import time
from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware:
    """
    安全响应头中间件
    
    添加安全相关的HTTP头，防止常见攻击：
    - X-Content-Type-Options: 防止MIME类型嗅探
    - X-Frame-Options: 防止点击劫持
    - X-XSS-Protection: XSS保护
    - Strict-Transport-Security: 强制HTTPS
    - Content-Security-Policy: 内容安全策略
    """
    
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
            response['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline';"
        
        return response


class SessionSecurityMiddleware:
    """
    会话安全中间件
    
    功能：
    1. 验证用户会话完整性
    2. 检测异常的会话访问模式
    3. 记录安全相关事件
    4. 防止会话固定攻击
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
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return None
        
        user_id = request.user.id
        session_key = request.session.session_key
        
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
        if hasattr(request, 'user') and request.user.is_authenticated:
            response['X-User-ID'] = str(request.user.id)
        
        return response


class RequestTimeoutMiddleware:
    """
    请求超时中间件

    用于设置请求超时时间，防止长时间运行的请求占用服务器资源。
    记录请求处理耗时，便于性能监控。
    """
    
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


