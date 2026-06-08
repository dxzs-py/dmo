"""
DRF 自定义速率限制类

使用 DRF 内置 throttling 框架替代原来的简单计数器中间件，
支持按用户/IP/接口分级限制，更精细、更灵活。

使用方式：
    - 在视图类中设置 throttle_classes
    - 或在 REST_FRAMEWORK DEFAULT_THROTTLE_CLASSES 中全局配置

限制策略：
    - AnonymousRateThrottle: 匿名用户（按 IP），较严格
    - UserRateThrottle: 已认证用户（按用户 ID），宽松一些
    - LoginRateThrottle: 登录接口，防止暴力破解，最严格
    - ChatStreamRateThrottle: 聊天流式接口，适度限制
    - ResearchRateThrottle: 深度研究接口，较严格
    - KnowledgeRateThrottle: 知识库接口，适度限制
    - SensitiveOperationRateThrottle: 敏感操作，最严格
"""
from rest_framework.throttling import SimpleRateThrottle


def get_client_ip(request):
    """从请求中提取客户端 IP 地址"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


class ScopedRateThrottle(SimpleRateThrottle):
    """
    限流基类：认证用户按 pk 限流，匿名用户按 IP 限流
    子类仅需声明 scope 属性
    """
    scope = None

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {
                'scope': self.scope,
                'ident': request.user.pk
            }
        return self.cache_format % {
            'scope': self.scope,
            'ident': get_client_ip(request)
        }


class AnonymousRateThrottle(SimpleRateThrottle):
    scope = 'anonymous'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': get_client_ip(request)
        }


class UserRateThrottle(SimpleRateThrottle):
    scope = 'user'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {
                'scope': self.scope,
                'ident': request.user.pk
            }
        return None


class LoginRateThrottle(SimpleRateThrottle):
    scope = 'login'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': get_client_ip(request)
        }


class ChatStreamRateThrottle(ScopedRateThrottle):
    scope = 'chat_stream'


class ResearchRateThrottle(ScopedRateThrottle):
    scope = 'research'


class KnowledgeRateThrottle(ScopedRateThrottle):
    scope = 'knowledge'


class SensitiveOperationRateThrottle(ScopedRateThrottle):
    scope = 'sensitive'
