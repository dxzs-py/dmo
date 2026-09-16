"""
统一 API 响应构建器

所有响应格式统一为 {code, message, data}。
仅承载成功响应构建；错误响应统一由全局 custom_exception_handler
（common/exceptions.py）在异常路径生成，视图层禁止手动构造。
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
