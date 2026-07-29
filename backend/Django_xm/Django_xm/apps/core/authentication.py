"""
DRF 认证类

提供查询参数 token 认证，用于 SSE、文件下载等无法设置 Authorization header 的场景。
"""

from rest_framework.authentication import BaseAuthentication

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class QueryParamTokenAuthentication(BaseAuthentication):
    """
    通过 URL 查询参数 token 进行 JWT 认证

    从 request.GET['token'] 获取 JWT，使用 simplejwt 的 JWTAuthentication
    进行验证，返回 (user, validated_token) 元组。
    """

    def authenticate(self, request):
        """认证请求，从查询参数 token 中提取并验证 JWT"""
        token = request.GET.get("token")
        if not token or len(token) >= 2048:
            return None

        try:
            from rest_framework import HTTP_HEADER_ENCODING
            from rest_framework_simplejwt.authentication import JWTAuthentication
            from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

            auth = JWTAuthentication()
            # 将查询参数 token 构造为 Bearer header 格式，复用 simplejwt 验证流程
            header_bytes = f"Bearer {token}".encode(HTTP_HEADER_ENCODING)
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
        """返回 WWW-Authenticate header 值"""
        return "Bearer"


__all__ = ["QueryParamTokenAuthentication"]
