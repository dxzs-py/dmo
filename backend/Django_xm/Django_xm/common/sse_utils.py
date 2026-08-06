"""
SSE 工具函数

提供 Server-Sent Events 端点通用的认证和响应工具。
所有 SSE 错误事件统一使用 {code, message, data} 格式，与 JSON 错误响应保持一致。
"""

import json
import logging

from django.http import StreamingHttpResponse
from rest_framework.renderers import BaseRenderer

logger = logging.getLogger(__name__)


class SSERenderer(BaseRenderer):
    """SSE 流式响应 Renderer。

    强制 content-type 为 text/event-stream，render 直接返回原始数据
    （由视图自行组装 "data: ...\\n\\n" 格式）。
    """

    media_type = "text/event-stream"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


def sse_error_event(code: str, message: str, data=None) -> str:
    """
    生成统一格式的 SSE 错误事件

    格式: event: error\\ndata: {"type": "error", "code": "5xxxx", "message": "错误描述", "data": null}\\n\\n
    data 行包含 type 字段，兼容前端手动 SSE 解析（仅解析 data: 行）和浏览器原生 EventSource。
    与 JSON 错误响应 {code, message, data} 结构一致（额外增加 type 字段用于前端事件路由）。

    Args:
        code: 错误码字符串，如 "50001"、"40101"
        message: 错误描述
        data: 附加数据，默认为 None

    Returns:
        SSE 格式字符串
    """
    error_payload = json.dumps(
        {"type": "error", "code": code, "message": message, "data": data},
        ensure_ascii=False,
    )
    return f"event: error\ndata: {error_payload}\n\n"


def authenticate_sse_request(request):
    """
    SSE端点统一认证：支持session、Authorization header、query param token

    Args:
        request: Django HttpRequest 对象

    Returns:
        已认证的 User 对象，或 None
    """
    from rest_framework import HTTP_HEADER_ENCODING
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    if request.user and request.user.is_authenticated:
        return request.user

    auth = JWTAuthentication()

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if auth_header.startswith("Bearer "):
        token_str = auth_header.split(" ", 1)[1]
        try:
            header_bytes = f"Bearer {token_str}".encode(HTTP_HEADER_ENCODING)
            raw_token = auth.get_raw_token(header_bytes)
            if raw_token:
                validated_token = auth.get_validated_token(raw_token)
                user = auth.get_user(validated_token)
                if user and user.is_authenticated:
                    return user
        except (InvalidToken, TokenError, Exception) as auth_err:
            logger.warning(f"[Auth] SSE端点Header Token验证失败: {auth_err}")

    token = request.GET.get("token")
    if token:
        try:
            header_bytes = f"Bearer {token}".encode(HTTP_HEADER_ENCODING)
            raw_token = auth.get_raw_token(header_bytes)
            if raw_token:
                validated_token = auth.get_validated_token(raw_token)
                user = auth.get_user(validated_token)
                if user and user.is_authenticated:
                    return user
        except (InvalidToken, TokenError, Exception) as auth_err:
            logger.warning(f"[Auth] SSE端点QueryParam Token验证失败: {auth_err}")

    return None


def authenticate_websocket_scope(scope):
    """WebSocket scope 统一认证：支持 query param token 与 Authorization header

    用于 ``AsyncJsonWebsocketConsumer.connect()`` 中通过 ``sync_to_async`` 调用。
    认证路径与 ``authenticate_sse_request`` 对齐（JWT），但输入是 Channels scope
    而非 Django HttpRequest，因此独立实现（scope 字段结构不同）。

    scope 认证优先级：
    1. ``scope['user']``（Channels AuthMiddleware 已认证）
    2. ``scope['headers']`` 中的 ``authorization: Bearer <token>``
    3. ``scope['query_string']`` 中的 ``token=<JWT>``

    Args:
        scope: Channels ASGI scope dict

    Returns:
        已认证的 User 对象，或 None
    """
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    # 1. AuthMiddleware 已认证（如配置了 channels 的 AuthMiddlewareStack）
    user = scope.get("user") if isinstance(scope, dict) else None
    if user and getattr(user, "is_authenticated", False):
        return user

    auth = JWTAuthentication()

    def _resolve_user_from_raw_token(raw_token):
        try:
            validated_token = auth.get_validated_token(raw_token)
            user = auth.get_user(validated_token)
            if user and user.is_authenticated:
                return user
        except (InvalidToken, TokenError, Exception) as auth_err:
            logger.warning(f"[Auth] WebSocket Token验证失败: {auth_err}")
        return None

    # 2. 从 headers 提取 Authorization: Bearer <token>
    headers = scope.get("headers") if isinstance(scope, dict) else None
    if headers:
        for name, value in headers:
            if name == b"authorization":
                try:
                    header_str = value.decode("latin-1") if isinstance(value, (bytes, bytearray)) else value
                    if header_str.startswith("Bearer "):
                        raw_token = auth.get_raw_token(f"Bearer {header_str.split(' ', 1)[1]}".encode("latin-1"))
                        if raw_token:
                            user = _resolve_user_from_raw_token(raw_token)
                            if user:
                                return user
                except Exception as header_err:
                    logger.warning(f"[Auth] WebSocket Header解析失败: {header_err}")
                break

    # 3. 从 query_string 提取 token=<JWT>
    query_string = scope.get("query_string") if isinstance(scope, dict) else None
    if query_string:
        try:
            qs_str = query_string.decode("latin-1") if isinstance(query_string, (bytes, bytearray)) else query_string
            # 简单解析 token 参数（不引入 urllib.parse 避免边界问题）
            token = None
            for pair in qs_str.split("&"):
                if pair.startswith("token="):
                    token = pair[len("token=") :]
                    break
            if token:
                raw_token = auth.get_raw_token(f"Bearer {token}".encode("latin-1"))
                if raw_token:
                    user = _resolve_user_from_raw_token(raw_token)
                    if user:
                        return user
        except Exception as qs_err:
            logger.warning(f"[Auth] WebSocket QueryString解析失败: {qs_err}")

    return None


def sse_error_response(message, status_code=401, code=None, data=None):
    """
    生成SSE错误响应，使用统一的 {code, message, data} 格式

    Args:
        message: 错误消息
        status_code: HTTP状态码
        code: 错误码字符串（默认根据 status_code 通过 ErrorCode 枚举反查）
        data: 附加数据

    Returns:
        StreamingHttpResponse 对象
    """
    if code is None:
        # Task 23.3：从 ErrorCode 枚举反查错误码，取代硬编码 _status_code_map
        # 单一真相源在 ErrorCode 枚举，新增错误码无需同步修改此处
        from Django_xm.common.error_codes import infer_error_code_from_http_status

        code = str(int(infer_error_code_from_http_status(status_code)))

    def error_event():
        yield sse_error_event(code=code, message=message, data=data)

    return StreamingHttpResponse(
        error_event(), content_type="text/event-stream", status=status_code, headers={"Cache-Control": "no-cache"}
    )


def sse_response(event_generator, headers=None):
    """
    创建统一的 StreamingHttpResponse 用于 SSE 端点。

    消除5个流式端点中重复的 StreamingHttpResponse 头设置。

    支持同步与异步生成器：Django 5.2 在 ASGI 部署下原生支持异步迭代器，
    在 WSGI 部署下会通过 async_to_sync 自动转换。

    Args:
        event_generator: 生成器/异步生成器，产生 "data: ...\\n\\n" 格式的 SSE 字符串
        headers: 额外响应头

    Returns:
        StreamingHttpResponse 对象
    """
    response_headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    if headers:
        response_headers.update(headers)
    return StreamingHttpResponse(
        event_generator,
        content_type="text/event-stream",
        headers=response_headers,
    )


async def sse_async_heartbeat_generator(async_gen, idle_timeout: float = 30.0):
    """为 async generator 添加心跳保活包装。

    当内部 generator 超过 ``idle_timeout`` 秒未产出事件时，自动发送
    ``{"type": "heartbeat"}`` 事件，防止代理/客户端因空闲超时断开连接。

    超时时不取消内部 pending 任务，确保长耗时操作（如 LLM 推理、工具执行）
    能继续执行；下一次循环复用同一 pending 任务。

    典型用法::

        return sse_response(
            sse_async_heartbeat_generator(
                chat_resume_generator._stream_chat_resume_generator(...)
            )
        )

    Args:
        async_gen: 异步生成器，产出 SSE 格式字符串（``"data: ...\\n\\n"``）
        idle_timeout: 空闲超时秒数，默认 30s

    Yields:
        SSE 格式字符串
    """
    import asyncio

    pending = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(async_gen.__anext__())

            done, _ = await asyncio.wait({pending}, timeout=idle_timeout)

            if not done:
                # 超时但任务仍在运行：发送心跳保活，不取消 pending
                yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                continue

            try:
                event = pending.result()
                pending = None
                yield event
            except StopAsyncIteration:
                pending = None
                break
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, Exception):  # noqa: S110  # cleanup, 取消后的 await 失败可忽略
                pass
        # 确保内部生成器被正确关闭，释放资源（如 checkpointer 连接）
        try:
            await async_gen.aclose()
        except Exception:  # noqa: S110  # cleanup, 生成器关闭失败可忽略
            pass
