"""
统一 API 响应构建器

所有响应格式统一为 {code, message, data}。
"""

from rest_framework.response import Response

from .error_codes import ErrorCode, get_error_message


def api_response(
    code=ErrorCode.SUCCESS,
    message=None,
    data=None,
    http_status=None,
    headers: dict[str, str] | None = None,
):
    """统一 API 响应构建

    Args:
        code: 错误码（int 或 ErrorCode 枚举）
        message: 响应消息，为空时从 ErrorCode 获取默认消息
        data: 响应数据
        http_status: HTTP 状态码，为空时从 ErrorCode.http_status 获取
        headers: 额外的 HTTP 响应头
    """
    if isinstance(code, ErrorCode):
        resolved_message = message or code.message
        resolved_status = http_status or code.http_status
        code_value = int(code)
    else:
        resolved_message = message or get_error_message(code)
        resolved_status = http_status or 200
        code_value = code

    response_data = {
        "code": code_value,
        "message": resolved_message,
        "data": data,
    }
    response = Response(response_data, status=resolved_status)
    if headers:
        for key, value in headers.items():
            response[key] = value
    return response


def success_response(data=None, message="操作成功", http_status=None, headers: dict[str, str] | None = None):
    """成功响应 (200)"""
    return api_response(
        code=ErrorCode.SUCCESS,
        message=message,
        data=data,
        http_status=http_status or 200,
        headers=headers,
    )


def error_response(
    code=ErrorCode.SERVER_ERROR, message=None, data=None, http_status=None, headers: dict[str, str] | None = None
):
    """错误响应"""
    return api_response(code=code, message=message, data=data, http_status=http_status, headers=headers)


def validation_error_response(errors, message="数据验证失败"):
    """验证错误响应 (400)"""
    return api_response(
        code=ErrorCode.VALIDATION_FAILED,
        message=message,
        data=errors,
    )


def not_found_response(message="资源不存在", data=None):
    """资源不存在响应 (404)"""
    return api_response(
        code=ErrorCode.NOT_FOUND,
        message=message,
        data=data,
    )
