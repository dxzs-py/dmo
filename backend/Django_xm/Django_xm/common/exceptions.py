"""
统一异常处理器

将所有 DRF/Python 异常转换为统一的 {code, message, data/details} 格式。
本模块是 DRF EXCEPTION_HANDLER 的唯一入口，避免循环依赖。
"""

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import status as http_status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    MethodNotAllowed,
    NotAuthenticated,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken

from .error_codes import ErrorCode

logger = logging.getLogger(__name__)


# Agent 异常 error_code → 业务 ErrorCode 映射
# 用于 _handle_agent_error 中将 LCAgentException.error_code 转换为统一业务错误码
_AGENT_ERROR_CODE_MAP = {
    "RATE_LIMIT_EXCEEDED": ErrorCode.AGENT_RATE_LIMITED,
    "AGENT_EXECUTION_ERROR": ErrorCode.AGENT_EXECUTION_FAILED,
    "MODEL_CALL_ERROR": ErrorCode.SERVICE_UNAVAILABLE,
    "RAG_RETRIEVAL_ERROR": ErrorCode.SERVER_ERROR,
    "CHECKPOINT_ERROR": ErrorCode.SERVER_ERROR,
    "GUARDRAILS_VALIDATION_ERROR": ErrorCode.VALIDATION_FAILED,
    "UNEXPECTED_ERROR": ErrorCode.SERVER_ERROR,
    "AGENT_ERROR": ErrorCode.SERVER_ERROR,
}


def custom_exception_handler(exc, context):
    """
    自定义异常处理器

    将所有异常转换为统一格式:
    - 成功: { code: 200, message: "操作成功", data: {...} }
    - 错误: { code: 错误码, message: "错误信息", data: {...} }
    """
    try:
        from Django_xm.apps.ai_engine.services.exceptions import LCAgentException

        if isinstance(exc, LCAgentException):
            return _handle_agent_error(exc)
    except ImportError:
        pass

    if isinstance(exc, ValidationError):
        return _handle_validation_error(exc)

    # MethodNotAllowed 必须在 APIException 通用分支前显式捕获，
    # 否则会被通用 APIException 分支以 500 返回，丢失 405 语义
    if isinstance(exc, MethodNotAllowed):
        return Response(
            {
                "code": int(ErrorCode.METHOD_NOT_ALLOWED),
                "message": str(exc.detail) if hasattr(exc, "detail") else "请求方法不允许",
            },
            status=http_status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        return _handle_auth_error(exc)

    # DRF PermissionDenied 是 APIException 子类，先于 Django PermissionDenied 匹配
    if isinstance(exc, PermissionDenied):
        return Response(
            {
                "code": int(ErrorCode.PERMISSION_DENIED),
                "message": str(exc.detail) if hasattr(exc, "detail") else "权限不足",
            },
            status=http_status.HTTP_403_FORBIDDEN,
        )

    # Django 核心 PermissionDenied（视图中 raise PermissionDenied 时抛出），
    # 非 APIException 子类，需显式捕获避免落到通用 500 分支
    if isinstance(exc, DjangoPermissionDenied):
        return Response(
            {
                "code": int(ErrorCode.PERMISSION_DENIED),
                "message": str(exc) or "权限不足",
            },
            status=http_status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, Throttled):
        return Response(
            {
                "code": int(ErrorCode.RATE_LIMITED),
                "message": f"请求过于频繁，请{exc.wait}秒后再试",
            },
            status=http_status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if isinstance(exc, Http404):
        return Response(
            {
                "code": int(ErrorCode.NOT_FOUND),
                "message": "请求的资源不存在",
            },
            status=http_status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, APIException):
        logger.warning(f"未处理的API异常: {type(exc).__name__}: {exc!s}")
        return Response(
            {
                "code": int(ErrorCode.SERVER_ERROR),
                "message": str(exc.detail) if hasattr(exc, "detail") else "服务器内部错误",
            },
            status=exc.status_code,
        )

    logger.error(f"未预期的异常: {type(exc).__name__}: {exc!s}", exc_info=True)
    return Response(
        {
            "code": int(ErrorCode.INTERNAL_ERROR),
            "message": "服务器内部错误，请稍后重试",
        },
        status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _handle_validation_error(exc: ValidationError) -> Response:
    if hasattr(exc, "detail"):
        detail = exc.detail
        if isinstance(detail, dict):
            errors = {}
            for field, messages in detail.items():
                if isinstance(messages, (list, tuple)):
                    errors[field] = [str(m) for m in messages]
                else:
                    errors[field] = str(messages)

            return Response(
                {
                    "code": int(ErrorCode.VALIDATION_FAILED),
                    "message": "数据验证失败",
                    "data": errors,
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        elif isinstance(detail, (list, tuple)):
            message = str(detail[0]) if detail else "数据验证失败"
        else:
            message = str(detail)
    else:
        message = str(exc)

    return Response(
        {
            "code": int(ErrorCode.VALIDATION_FAILED),
            "message": message,
        },
        status=http_status.HTTP_400_BAD_REQUEST,
    )


def _handle_auth_error(exc) -> Response:
    """认证异常处理

    使用 ``isinstance(exc, InvalidToken)`` 精确识别 SimpleJWT 抛出的 Token 异常。
    ``InvalidToken`` 继承自 ``AuthenticationFailed``，因此会进入本函数；
    SimpleJWT 在 view 层将 ``TokenError`` 转为 ``InvalidToken`` 抛出，故本处仅检查
    ``InvalidToken``，避免误判其他 ``AuthenticationFailed`` 子类。

    ``InvalidToken.detail`` 为 dict 结构（含 'detail' 与 'code' 键），
    需从 ``detail['detail']`` 提取实际错误消息再判断是否为过期。
    """
    # 提取错误消息：InvalidToken.detail 为 dict，需取 'detail' 子键
    raw_detail = getattr(exc, "detail", None)
    if isinstance(raw_detail, dict) and "detail" in raw_detail:
        error_message = str(raw_detail["detail"])
    elif raw_detail is not None:
        error_message = str(raw_detail)
    else:
        error_message = str(exc)

    if isinstance(exc, InvalidToken):
        # SimpleJWT 的 InvalidToken 不区分子类（expired/invalid），
        # 需通过消息关键字判断是否过期
        msg_lower = error_message.lower() if error_message else ""
        if "expired" in msg_lower or " exp" in msg_lower or msg_lower.endswith("exp"):
            code = ErrorCode.TOKEN_EXPIRED
            message = "登录已过期，请重新登录"
        else:
            code = ErrorCode.TOKEN_INVALID
            message = "无效的认证信息，请重新登录"
    else:
        code = ErrorCode.UNAUTHORIZED
        message = error_message or "未登录或登录已过期"

    return Response(
        {
            "code": int(code),
            "message": message,
        },
        status=http_status.HTTP_401_UNAUTHORIZED,
    )


def _handle_agent_error(exc) -> Response:
    """将 LCAgentException 映射到统一业务错误码

    根据 exc.error_code 在 _AGENT_ERROR_CODE_MAP 中查找业务错误码；
    未映射的 error_code 回退到 SERVER_ERROR。
    HTTP 状态码优先级：
      1. 不可恢复 → 503
      2. RATE_LIMIT_EXCEEDED → 429
      3. GUARDRAILS_VALIDATION_ERROR → 400
      4. 其他 → 500
    """
    error_data = exc.to_dict()
    business_code = _AGENT_ERROR_CODE_MAP.get(exc.error_code, ErrorCode.SERVER_ERROR)

    # HTTP 状态码：根据业务错误码的 http_status 与 recoverable 综合判定
    if not exc.recoverable:
        status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
    elif business_code == ErrorCode.AGENT_RATE_LIMITED:
        status_code = http_status.HTTP_429_TOO_MANY_REQUESTS
    elif business_code == ErrorCode.VALIDATION_FAILED:
        status_code = http_status.HTTP_400_BAD_REQUEST
    elif business_code == ErrorCode.SERVICE_UNAVAILABLE:
        status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR

    return Response(
        {
            "code": int(business_code),
            "message": exc.user_message or error_data.get("message", "服务器内部错误"),
            "data": {
                "error_code": error_data.get("error_code"),
                "recoverable": error_data.get("recoverable", True),
                **error_data.get("details", {}),
            },
        },
        status=status_code,
    )
