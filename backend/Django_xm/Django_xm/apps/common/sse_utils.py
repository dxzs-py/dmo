"""
SSE 工具函数

提供 Server-Sent Events 端点通用的认证和响应工具。
"""

import json
import logging

from django.http import StreamingHttpResponse

logger = logging.getLogger(__name__)


def authenticate_sse_request(request):
    """
    SSE端点统一认证：支持session、Authorization header、query param token

    Args:
        request: Django HttpRequest 对象

    Returns:
        已认证的 User 对象，或 None
    """
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
    from rest_framework import HTTP_HEADER_ENCODING

    if request.user and request.user.is_authenticated:
        return request.user

    auth = JWTAuthentication()

    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if auth_header.startswith('Bearer '):
        token_str = auth_header.split(' ', 1)[1]
        try:
            header_bytes = f'Bearer {token_str}'.encode(HTTP_HEADER_ENCODING)
            raw_token = auth.get_raw_token(header_bytes)
            if raw_token:
                validated_token = auth.get_validated_token(raw_token)
                user = auth.get_user(validated_token)
                if user and user.is_authenticated:
                    return user
        except (InvalidToken, TokenError, Exception) as auth_err:
            logger.warning(f"[Auth] SSE端点Header Token验证失败: {auth_err}")

    token = request.GET.get('token')
    if token:
        try:
            header_bytes = f'Bearer {token}'.encode(HTTP_HEADER_ENCODING)
            raw_token = auth.get_raw_token(header_bytes)
            if raw_token:
                validated_token = auth.get_validated_token(raw_token)
                user = auth.get_user(validated_token)
                if user and user.is_authenticated:
                    return user
        except (InvalidToken, TokenError, Exception) as auth_err:
            logger.warning(f"[Auth] SSE端点QueryParam Token验证失败: {auth_err}")

    return None


def sse_error_response(message, status_code=401):
    """
    生成SSE错误响应

    Args:
        message: 错误消息
        status_code: HTTP状态码

    Returns:
        StreamingHttpResponse 对象
    """
    def error_event():
        yield f"data: {json.dumps({'type': 'error', 'message': message}, ensure_ascii=False)}\n\n"
    return StreamingHttpResponse(
        error_event(),
        content_type='text/event-stream',
        status=status_code,
        headers={'Cache-Control': 'no-cache'}
    )


def sse_response(event_generator, headers=None):
    """
    创建统一的 StreamingHttpResponse 用于 SSE 端点。

    消除5个流式端点中重复的 StreamingHttpResponse 头设置。

    Args:
        event_generator: 生成器/异步生成器，产生 "data: ...\\n\\n" 格式的 SSE 字符串
        headers: 额外响应头

    Returns:
        StreamingHttpResponse 对象
    """
    response_headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    }
    if headers:
        response_headers.update(headers)
    return StreamingHttpResponse(
        event_generator,
        content_type='text/event-stream',
        headers=response_headers,
    )
