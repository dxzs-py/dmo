"""
统一响应工具模块
所有API响应格式: { code, message, data }
code: ErrorCode枚举值 (200=成功)
message: 响应消息
data: 响应数据
"""
from rest_framework.response import Response
from rest_framework import status as _status
from typing import Any, Dict, Optional, List

from .error_codes import ErrorCode, get_error_message


def _infer_http_status(code: int) -> int:
    if code == ErrorCode.SUCCESS:
        return _status.HTTP_200_OK
    elif 40001 <= code <= 40005:
        return _status.HTTP_400_BAD_REQUEST
    elif 40101 <= code <= 40105:
        return _status.HTTP_401_UNAUTHORIZED
    elif 40301 <= code <= 40303:
        return _status.HTTP_403_FORBIDDEN
    elif 40401 <= code <= 40404:
        return _status.HTTP_404_NOT_FOUND
    elif code in (42901, 42902):
        return _status.HTTP_429_TOO_MANY_REQUESTS
    else:
        return _status.HTTP_500_INTERNAL_SERVER_ERROR


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
        http_status = _infer_http_status(int(code))

    return Response(response_data, status=http_status, headers=headers)


def success_response(
    data: Any = None,
    message: str = "操作成功",
    status_code: int = None,
    headers: Dict = None,
) -> Response:
    if status_code is None:
        status_code = _status.HTTP_200_OK
    return api_response(
        code=ErrorCode.SUCCESS,
        message=message,
        data=data,
        http_status=status_code,
        headers=headers,
    )


def created_response(
    data: Any = None,
    message: str = "创建成功",
) -> Response:
    return success_response(data, message, _status.HTTP_201_CREATED)


def error_response(
    code: ErrorCode = ErrorCode.SERVER_ERROR,
    message: str = None,
    details: Any = None,
    data: Any = None,
    http_status: int = None,
) -> Response:
    """
    错误响应快捷方法

    Args:
        code: 错误码
        message: 错误消息
        details: 详细错误信息（向后兼容，等价于data）
        data: 错误详情数据
        http_status: HTTP状态码
    """
    error_data = data if data is not None else details

    response_data = {
        'code': int(code),
        'message': message or get_error_message(code),
    }

    if error_data is not None:
        response_data['data'] = error_data

    if http_status is None:
        http_status = _infer_http_status(int(code))

    return Response(response_data, status=http_status)


def paginated_response(
    results: List[Any],
    count: int,
    page: int,
    page_size: int,
    message: str = "获取成功",
) -> Response:
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
    return error_response(
        code=ErrorCode.VALIDATION_FAILED,
        message=message,
        details=errors,
    )


def not_found_response(
    message: str = "资源不存在",
    details: Any = None,
) -> Response:
    return error_response(
        code=ErrorCode.NOT_FOUND,
        message=message,
        details=details,
        http_status=_status.HTTP_404_NOT_FOUND,
    )
