"""
基础视图类
提供通用的视图基类和服务层基类
"""
import logging
from typing import Any, Dict, Optional, Type
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from .responses import success_response, error_response, not_found_response
from .error_codes import ErrorCode

logger = logging.getLogger(__name__)


class BaseAPIView(APIView):
    """
    基础 API 视图类
    提供通用的视图功能
    """
    
    serializer_class: Optional[Type[Serializer]] = None
    
    def get_serializer(self, *args, **kwargs) -> Serializer:
        """
        获取序列化器实例
        """
        if self.serializer_class is None:
            raise NotImplementedError("需要设置 serializer_class")
        return self.serializer_class(*args, **kwargs)
    
    def validate_data(self, data: Dict[str, Any]) -> Optional[Response]:
        """
        验证请求数据
        
        Args:
            data: 请求数据
        
        Returns:
            如果验证失败返回错误响应，成功返回 None
        """
        if self.serializer_class is None:
            return None
        
        serializer = self.get_serializer(data=data)
        if not serializer.is_valid():
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message="数据验证失败",
                data=serializer.errors,
                http_status=status.HTTP_400_BAD_REQUEST
            )
        return None
    
    def handle_exception(self, exc: Exception) -> Response:
        """
        统一处理异常
        """
        logger.error(f"视图异常: {str(exc)}", exc_info=True)
        return super().handle_exception(exc)


class BaseService:
    """
    基础服务类
    提供通用的服务层功能
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__module__)
    
    def log_info(self, message: str, **kwargs) -> None:
        """记录信息日志"""
        self.logger.info(message, extra=kwargs)
    
    def log_error(self, message: str, **kwargs) -> None:
        """记录错误日志"""
        self.logger.error(message, extra=kwargs, exc_info=True)
    
    def log_warning(self, message: str, **kwargs) -> None:
        """记录警告日志"""
        self.logger.warning(message, extra=kwargs)
