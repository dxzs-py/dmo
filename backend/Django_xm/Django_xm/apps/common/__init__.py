"""
公共模块

提供项目级别的通用工具：
- responses: 统一API响应格式
- error_codes: 错误码枚举
- exceptions: 异常处理器
- base_views: 基础视图/服务基类
"""

from .responses import (
    api_response,
    success_response,
    created_response,
    error_response,
    paginated_response,
    validation_error_response,
    not_found_response,
)
from .error_codes import ErrorCode, get_error_message
from .exceptions import custom_exception_handler
from .base_views import BaseAPIView, BaseService
from .request_utils import get_client_ip, get_user_agent

__all__ = [
    "api_response",
    "success_response",
    "created_response",
    "error_response",
    "paginated_response",
    "validation_error_response",
    "not_found_response",
    "ErrorCode",
    "get_error_message",
    "custom_exception_handler",
    "BaseAPIView",
    "BaseService",
    "get_client_ip",
    "get_user_agent",
]
