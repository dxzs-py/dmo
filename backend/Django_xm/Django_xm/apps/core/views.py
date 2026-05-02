"""
自定义异常处理器 + 核心视图
将DRF/Python异常转换为统一的 {code, message, data/details} 格式
"""
import logging
import time
from typing import Optional

from django.http import Http404, HttpRequest, JsonResponse
from rest_framework.views import exception_handler
from rest_framework.exceptions import (
    ValidationError,
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    Throttled,
    APIException,
)
from rest_framework import status as http_status
from rest_framework.response import Response

from Django_xm.apps.common.error_codes import ErrorCode

logger = logging.getLogger(__name__)


def health_check(request):
    """健康检查接口"""
    return JsonResponse({
        'status': 'healthy',
        'timestamp': time.time(),
    })


def request_monitor(request):
    """请求监控中间件视图"""
    if request.method == 'OPTIONS':
        return JsonResponse({}, status=http_status.HTTP_200_OK)
    return JsonResponse({'message': 'request_monitor'})


def custom_exception_handler(exc, context):
    """
    自定义异常处理器
    
    将所有异常转换为统一格式:
    - 成功: { code: 0, message: "操作成功", data: {...} }
    - 错误: { code: 错误码, message: "错误信息", details: {...} }
    
    前端兼容性:
    - data.code === 0 表示成功（与前端 client.js 的判断逻辑一致）
    - data.code !== 0 表示失败，data.message 包含错误描述
    """
    if isinstance(exc, ValidationError):
        return _handle_validation_error(exc)

    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        return _handle_auth_error(exc)

    if isinstance(exc, PermissionDenied):
        return Response({
            'code': int(ErrorCode.PERMISSION_DENIED),
            'message': str(exc.detail) if hasattr(exc, 'detail') else "权限不足",
        }, status=http_status.HTTP_403_FORBIDDEN)

    if isinstance(exc, Throttled):
        return Response({
            'code': int(ErrorCode.RATE_LIMITED),
            'message': f"请求过于频繁，请{exc.wait}秒后再试",
        }, status=http_status.HTTP_429_TOO_MANY_REQUESTS)

    if isinstance(exc, Http404):
        return Response({
            'code': int(ErrorCode.NOT_FOUND),
            'message': "请求的资源不存在",
        }, status=http_status.HTTP_404_NOT_FOUND)

    if isinstance(exc, APIException):
        logger.warning(f"未处理的API异常: {type(exc).__name__}: {str(exc)}")
        return Response({
            'code': int(ErrorCode.SERVER_ERROR),
            'message': str(exc.detail) if hasattr(exc, 'detail') else "服务器内部错误",
        }, status=exc.status_code)

    logger.error(f"未预期的异常: {type(exc).__name__}: {str(exc)}", exc_info=True)
    return Response({
        'code': int(ErrorCode.INTERNAL_ERROR),
        'message': "服务器内部错误，请稍后重试",
    }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)


def _handle_validation_error(exc: ValidationError) -> Response:
    """处理验证错误"""
    if hasattr(exc, 'detail'):
        detail = exc.detail
        if isinstance(detail, dict):
            errors = {}
            for field, messages in detail.items():
                if isinstance(messages, (list, tuple)):
                    errors[field] = [str(m) for m in messages]
                else:
                    errors[field] = str(messages)

            return Response({
                'code': int(ErrorCode.VALIDATION_FAILED),
                'message': "数据验证失败",
                'details': errors,
            }, status=http_status.HTTP_400_BAD_REQUEST)
        elif isinstance(detail, (list, tuple)):
            message = str(detail[0]) if detail else "数据验证失败"
        else:
            message = str(detail)
    else:
        message = str(exc)

    return Response({
        'code': int(ErrorCode.VALIDATION_FAILED),
        'message': message,
    }, status=http_status.HTTP_400_BAD_REQUEST)


def _handle_auth_error(exc) -> Response:
    """处理认证错误"""
    error_message = str(exc.detail) if hasattr(exc, 'detail') else str(exc)

    if "Token is expired" in error_message or "expired" in error_message.lower():
        code = ErrorCode.TOKEN_EXPIRED
        message = "登录已过期，请重新登录"
    elif "Invalid token" in error_message or "invalid" in error_message.lower() or "not valid" in error_message.lower():
        code = ErrorCode.TOKEN_INVALID
        message = "无效的认证信息，请重新登录"
    else:
        code = ErrorCode.UNAUTHORIZED
        message = error_message or "未登录或登录已过期"

    return Response({
        'code': int(code),
        'message': message,
    }, status=http_status.HTTP_401_UNAUTHORIZED)

