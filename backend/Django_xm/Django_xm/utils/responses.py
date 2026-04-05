"""
统一响应工具模块
所有API响应格式: { code, message, data }
code: ErrorCode枚举值 (0=成功)
message: 响应消息
data: 响应数据(成功时) 或 details(失败时)
"""
from rest_framework.response import Response
from rest_framework import status as http_status
from typing import Any, Dict, Optional, List

from .error_codes import ErrorCode, get_error_message


def api_response(
    code: ErrorCode = ErrorCode.SUCCESS,
    message: str = None,
    data: Any = None,
    http_status: int = None,
    headers: Dict = None,
) -> Response:
    """
    统一API响应构建器

    Args:
        code: 错误码 (ErrorCode.SUCCESS 表示成功)
        message: 响应消息（默认从错误码获取）
        data: 响应数据
        http_status: HTTP状态码（默认根据code自动推断）
        headers: 额外响应头

    Returns:
        DRF Response对象，格式: { code, message, data }
    """
    response_data = {
        'code': int(code),
        'message': message or get_error_message(code),
    }

    if data is not None:
        response_data['data'] = data

    if http_status is None:
        if code == ErrorCode.SUCCESS:
            http_status = http_status.HTTP_200_OK
        elif 40001 <= code <= 40005:
            http_status = http_status.HTTP_400_BAD_REQUEST
        elif 40101 <= code <= 40105:
            http_status = http_status.HTTP_401_UNAUTHORIZED
        elif 40301 <= code <= 40303:
            http_status = http_status.HTTP_403_FORBIDDEN
        elif 40401 <= code <= 40404:
            http_status = http_status.HTTP_404_NOT_FOUND
        elif code in (42901, 42902):
            http_status = http_status.HTTP_429_TOO_MANY_REQUESTS
        else:
            http_status = http_status.HTTP_500_INTERNAL_SERVER_RESPONSE

    return Response(response_data, status=http_status, headers=headers)


def success_response(
    data: Any = None,
    message: str = "操作成功",
    status_code: int = http_status.HTTP_200_OK,
) -> Response:
    """成功响应快捷方法"""
    return api_response(
        code=ErrorCode.SUCCESS,
        message=message,
        data=data,
        http_status=status_code,
    )


def created_response(
    data: Any = None,
    message: str = "创建成功",
) -> Response:
    """创建成功响应 (201)"""
    return success_response(data, message, http_status.HTTP_201_CREATED)


def error_response(
    code: ErrorCode = ErrorCode.SERVER_ERROR,
    message: str = None,
    details: Any = None,
    http_status: int = None,
) -> Response:
    """
    错误响应快捷方法
    
    Args:
        code: 错误码
        message: 错误消息
        details: 详细错误信息（如字段级验证错误）
        http_status: HTTP状态码
    """
    response_data = {
        'code': int(code),
        'message': message or get_error_message(code),
    }

    if details is not None:
        response_data['details'] = details

    if http_status is None:
        if 40001 <= code <= 40005:
            http_status = http_status.HTTP_400_BAD_REQUEST
        elif 40101 <= code <= 40105:
            http_status = http_status.HTTP_401_UNAUTHORIZED
        elif 40301 <= code <= 40303:
            http_status = http_status.HTTP_403_FORBIDDEN
        elif 40401 <= code <= 40404:
            http_status = http_status.HTTP_404_NOT_FOUND
        elif code in (42901, 42902):
            http_status = http_status.HTTP_429_TOO_MANY_REQUESTS
        else:
            http_status = http_status.HTTP_500_INTERNAL_SERVER_ERROR

    return Response(response_data, status=http_status)


def paginated_response(
    results: List[Any],
    count: int,
    page: int,
    page_size: int,
    message: str = "获取成功",
) -> Response:
    """分页响应"""
    total_pages = (count + page_size - 1) // page_size if page_size > 0 else 0

    data = {
        'results': results,
        'pagination': {
            'count': count,
            'page': page,
            'page_size': page_size,
            'total_pages': total_pages
        }
    }
    return success_response(data, message)


def validation_error_response(
    errors: Dict[str, List[str]],
    message: str = "数据验证失败",
) -> Response:
    """验证错误响应"""
    return error_response(
        code=ErrorCode.VALIDATION_FAILED,
        message=message,
        details=errors,
    )


def not_found_response(
    message: str = "资源不存在",
    details: Any = None,
) -> Response:
    """404未找到响应"""
    return error_response(
        code=ErrorCode.NOT_FOUND,
        message=message,
        details=details,
        http_status=http_status.HTTP_404_NOT_FOUND,
    )
