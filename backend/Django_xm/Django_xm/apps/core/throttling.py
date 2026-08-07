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

IP 解析：
    ``get_client_ip`` 统一从 ``Django_xm.common.request_utils`` 导入，
    配合 ``settings.NUM_PROXIES`` 防 X-Forwarded-For 伪造（详见该函数 docstring）。
"""

from rest_framework.throttling import SimpleRateThrottle

from Django_xm.common.request_utils import get_client_ip

__all__ = [
    "AnonymousRateThrottle",
    "ChatStreamRateThrottle",
    "KnowledgeRateThrottle",
    "LoginRateThrottle",
    "MetaRateThrottle",
    "ResearchRateThrottle",
    "ScopedRateThrottle",
    "SensitiveOperationRateThrottle",
    "SnapshotRateThrottle",
    "UserRateThrottle",
]


class ScopedRateThrottle(SimpleRateThrottle):
    """
    限流基类：认证用户按 pk 限流，匿名用户按 IP 限流
    子类仅需声明 scope 属性
    """

    scope = None

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {"scope": self.scope, "ident": request.user.pk}
        return self.cache_format % {"scope": self.scope, "ident": get_client_ip(request)}


class AnonymousRateThrottle(SimpleRateThrottle):
    scope = "anonymous"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return None
        return self.cache_format % {"scope": self.scope, "ident": get_client_ip(request)}


class UserRateThrottle(SimpleRateThrottle):
    scope = "user"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {"scope": self.scope, "ident": request.user.pk}
        return None


class LoginRateThrottle(SimpleRateThrottle):
    scope = "login"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": get_client_ip(request)}


class ChatStreamRateThrottle(ScopedRateThrottle):
    scope: str = "chat_stream"  # type: ignore[assignment]  # django-stubs types scope as None


class SnapshotRateThrottle(ScopedRateThrottle):
    """会话快照校对接口（全量聚合，开销大），独立额度避免与普通请求抢 user 池。

    按用户粒度计数（复用 UserRateThrottle 模式）：同一用户多浏览器共享额度，
    跨账号隔离；未认证请求不计数（返回 None）。避免多浏览器同 IP 互相挤兑额度。
    """

    scope: str = "snapshot"  # type: ignore[assignment]  # django-stubs types scope as None

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {"scope": self.scope, "ident": request.user.pk}
        return None


class MetaRateThrottle(ScopedRateThrottle):
    """页面加载即请求的只读元数据接口，独立额度避免与用户会话操作共享 user 池。"""

    scope: str = "meta"  # type: ignore[assignment]  # django-stubs types scope as None


class ResearchRateThrottle(ScopedRateThrottle):
    scope: str = "research"  # type: ignore[assignment]  # django-stubs types scope as None


class KnowledgeRateThrottle(ScopedRateThrottle):
    scope: str = "knowledge"  # type: ignore[assignment]  # django-stubs types scope as None


class SensitiveOperationRateThrottle(ScopedRateThrottle):
    scope: str = "sensitive"  # type: ignore[assignment]  # django-stubs types scope as None
