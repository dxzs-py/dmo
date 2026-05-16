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
import time
from rest_framework.throttling import SimpleRateThrottle


class AnonymousRateThrottle(SimpleRateThrottle):
    scope = 'anonymous'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return None
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return self.cache_format % {
            'scope': self.scope,
            'ident': ip
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
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return self.cache_format % {
            'scope': self.scope,
            'ident': ip
        }


class ChatStreamRateThrottle(SimpleRateThrottle):
    scope = 'chat_stream'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            ident = (x_forwarded_for.split(',')[0].strip()
                     if x_forwarded_for
                     else request.META.get('REMOTE_ADDR'))
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }


class ResearchRateThrottle(SimpleRateThrottle):
    scope = 'research'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            ident = (x_forwarded_for.split(',')[0].strip()
                     if x_forwarded_for
                     else request.META.get('REMOTE_ADDR'))
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }


class KnowledgeRateThrottle(SimpleRateThrottle):
    scope = 'knowledge'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            ident = (x_forwarded_for.split(',')[0].strip()
                     if x_forwarded_for
                     else request.META.get('REMOTE_ADDR'))
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }


class SensitiveOperationRateThrottle(SimpleRateThrottle):
    scope = 'sensitive'

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            ident = (x_forwarded_for.split(',')[0].strip()
                     if x_forwarded_for
                     else request.META.get('REMOTE_ADDR'))
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }
