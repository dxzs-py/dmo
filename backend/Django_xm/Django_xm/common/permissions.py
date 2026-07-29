"""
自定义 DRF 权限类

提供项目级别的可复用权限检查：
- IsAdmin: 仅管理员可访问
- IsAuthenticatedOrQueryParam: 支持标准认证或查询参数 token 认证
"""

from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """仅管理员可访问（is_staff=True）"""

    def has_permission(self, request, view):
        return request.user and request.user.is_staff


class IsAuthenticatedOrQueryParam(BasePermission):
    """支持标准认证或查询参数 token 认证的权限类

    用于 SSE、文件下载等无法设置 Authorization header 的场景
    """

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        from Django_xm.apps.core.authentication import QueryParamTokenAuthentication

        authenticator = QueryParamTokenAuthentication()
        result = authenticator.authenticate(request)
        if result is not None:
            user, _ = result
            request.user = user
            return True
        return False
