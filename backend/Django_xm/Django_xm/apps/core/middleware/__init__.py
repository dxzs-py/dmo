"""
核心中间件模块

按职责拆分为独立子模块，通过 __init__.py 统一导出以保持向后兼容。
"""

from Django_xm.apps.core.middleware.cache import CacheControlMiddleware
from Django_xm.apps.core.middleware.request import APIRequestMiddleware, CurrentRequestMiddleware
from Django_xm.apps.core.middleware.security import SecurityHeadersMiddleware, SessionSecurityMiddleware

__all__ = [
    "APIRequestMiddleware",
    "CacheControlMiddleware",
    "CurrentRequestMiddleware",
    "SecurityHeadersMiddleware",
    "SessionSecurityMiddleware",
]
