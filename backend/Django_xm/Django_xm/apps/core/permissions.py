"""
DRF 权限类 + 工具权限系统重新导出

DRF 权限类:
  - IsAuthenticatedOrQueryParam: 支持标准认证或查询参数 token 认证
  - QueryParamTokenAuthentication: 查询参数 token 认证

工具权限系统已拆分到 tool_permissions.py，本文件重新导出以保持向后兼容。
"""

import logging

from rest_framework.permissions import BasePermission

from Django_xm.apps.config_center.config import get_logger

logger = get_logger(__name__)


class QueryParamTokenAuthentication:
    def authenticate(self, request):
        token = request.GET.get('token')
        if not token or len(token) >= 2048:
            return None

        try:
            from rest_framework_simplejwt.authentication import JWTAuthentication
            from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
            from rest_framework import HTTP_HEADER_ENCODING

            auth = JWTAuthentication()
            header_bytes = f'Bearer {token}'.encode(HTTP_HEADER_ENCODING)
            raw_token = auth.get_raw_token(header_bytes)
            if raw_token:
                validated_token = auth.get_validated_token(raw_token)
                user = auth.get_user(validated_token)
                if user and user.is_authenticated:
                    return (user, validated_token)
        except (InvalidToken, TokenError) as e:
            logger.warning(f"查询参数token认证失败: {e}")
        except Exception as e:
            logger.warning(f"查询参数token验证异常: {e}")

        return None

    def authenticate_header(self, request):
        return 'Bearer'


class IsAuthenticatedOrQueryParam(BasePermission):
    """
    支持标准认证或查询参数 token 认证的权限类
    用于 SSE、文件下载等无法设置 Authorization header 的场景
    """
    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True

        token = request.GET.get('token')
        if token and len(token) < 2048:
            try:
                from rest_framework_simplejwt.authentication import JWTAuthentication
                from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
                from rest_framework import HTTP_HEADER_ENCODING

                auth = JWTAuthentication()
                header_bytes = f'Bearer {token}'.encode(HTTP_HEADER_ENCODING)
                raw_token = auth.get_raw_token(header_bytes)
                if raw_token:
                    validated_token = auth.get_validated_token(raw_token)
                    user = auth.get_user(validated_token)
                    if user and user.is_authenticated:
                        request.user = user
                        return True
            except Exception as e:
                logger.warning(f"查询参数token验证失败: {e}")

        return False


from .tool_permissions import (  # noqa: E402
    PermissionMode,
    SessionPermissionMode,
    PermissionPolicy,
    PermissionService,
    READ_ONLY_TOOLS,
    WRITE_TOOLS,
    DANGEROUS_TOOLS,
    SESSION_MODE_ALLOWED_TOOLS,
    get_pending_confirmation,
    approve_tool_confirmation,
    deny_tool_confirmation,
    cleanup_expired_confirmations,
)

__all__ = [
    'QueryParamTokenAuthentication',
    'IsAuthenticatedOrQueryParam',
    'PermissionMode',
    'SessionPermissionMode',
    'PermissionPolicy',
    'PermissionService',
    'READ_ONLY_TOOLS',
    'WRITE_TOOLS',
    'DANGEROUS_TOOLS',
    'SESSION_MODE_ALLOWED_TOOLS',
    'get_pending_confirmation',
    'approve_tool_confirmation',
    'deny_tool_confirmation',
    'cleanup_expired_confirmations',
]
