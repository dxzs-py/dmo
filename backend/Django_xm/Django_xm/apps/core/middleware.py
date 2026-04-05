import logging
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from django.contrib.auth import authenticate
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    安全响应头中间件
    
    添加安全相关的HTTP头，防止常见攻击：
    - X-Content-Type-Options: 防止MIME类型嗅探
    - X-Frame-Options: 防止点击劫持
    - X-XSS-Protection: XSS保护
    - Strict-Transport-Security: 强制HTTPS
    - Content-Security-Policy: 内容安全策略
    """
    
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


class SessionSecurityMiddleware(MiddlewareMixin):
    """
    会话安全中间件
    
    功能：
    1. 验证用户会话完整性
    2. 检测异常的会话访问模式
    3. 记录安全相关事件
    4. 防止会话固定攻击
    """
    
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
            
            from django.contrib.auth import logout
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
        if request.user.is_authenticated:
            response['X-User-ID'] = str(request.user.id)
        
        return response


class RateLimitMiddleware(MiddlewareMixin):
    """
    简单的API速率限制中间件
    
    防止暴力破解和滥用API
    """
    
    def process_request(self, request):
        if not request.path.startswith('/api/'):
            return None
        
        from django.core.cache import cache
        
        client_ip = self._get_client_ip(request)
        cache_key = f'rate_limit:{client_ip}'
        
        requests_count = cache.get(cache_key, 0)
        
        if requests_count >= 100:
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            return JsonResponse({
                'code': 429,
                'message': '请求过于频繁，请稍后再试',
                'error': 'RATE_LIMIT_EXCEEDED'
            }, status=429)
        
        cache.set(cache_key, requests_count + 1, timeout=60)
        
        return None
    
    @staticmethod
    def _get_client_ip(request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
