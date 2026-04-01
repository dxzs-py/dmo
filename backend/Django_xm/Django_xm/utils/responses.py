"""
响应工具模块
提供统一的API响应格式
"""
from rest_framework.response import Response
from rest_framework import status
from typing import Any, Dict, Optional, List


def success_response(
    data: Any = None,
    message: str = "操作成功",
    status_code: int = status.HTTP_200_OK
) -> Response:
    """
    成功响应
    
    Args:
        data: 响应数据
        message: 响应消息
        status_code: HTTP状态码
    
    Returns:
        DRF Response对象
    """
    response_data = {
        'success': True,
        'message': message
    }
    
    if data is not None:
        response_data['data'] = data
    
    return Response(response_data, status=status_code)


def error_response(
    error: str,
    message: Optional[str] = None,
    status_code: int = status.HTTP_400_BAD_REQUEST,
    details: Optional[Dict] = None
) -> Response:
    """
    错误响应
    
    Args:
        error: 错误信息
        message: 错误消息
        status_code: HTTP状态码
        details: 详细错误信息
    
    Returns:
        DRF Response对象
    """
    response_data = {
        'success': False,
        'error': error
    }
    
    if message:
        response_data['message'] = message
    
    if details:
        response_data['details'] = details
    
    return Response(response_data, status=status_code)


def created_response(
    data: Any = None,
    message: str = "创建成功"
) -> Response:
    """
    创建成功响应 (201 Created)
    
    Args:
        data: 创建的数据
        message: 响应消息
    
    Returns:
        DRF Response对象
    """
    return success_response(data, message, status.HTTP_201_CREATED)


def not_found_response(
    error: str = "资源不存在",
    message: Optional[str] = None
) -> Response:
    """
    404 响应
    
    Args:
        error: 错误信息
        message: 错误消息
    
    Returns:
        DRF Response对象
    """
    return error_response(error, message, status.HTTP_404_NOT_FOUND)


def validation_error_response(
    errors: Dict[str, List[str]],
    message: str = "数据验证失败"
) -> Response:
    """
    验证错误响应
    
    Args:
        errors: 验证错误字典
        message: 错误消息
    
    Returns:
        DRF Response对象
    """
    return error_response(
        error=message,
        status_code=status.HTTP_400_BAD_REQUEST,
        details=errors
    )


def paginated_response(
    results: List[Any],
    count: int,
    page: int,
    page_size: int,
    message: str = "获取成功"
) -> Response:
    """
    分页响应
    
    Args:
        results: 结果列表
        count: 总数
        page: 当前页码
        page_size: 每页数量
        message: 响应消息
    
    Returns:
        DRF Response对象
    """
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
