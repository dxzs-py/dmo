"""
自定义 DRF 权限类

提供项目级别的可复用权限检查：
- IsAdmin: 仅管理员可访问
"""

from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """仅管理员可访问（is_staff=True）"""

    def has_permission(self, request, view):
        return request.user and request.user.is_staff
