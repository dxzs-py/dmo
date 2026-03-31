"""
核心视图模块
提供健康检查、监控等通用功能
"""
import time
import logging
from datetime import datetime
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

from .config import get_logger

logger = get_logger(__name__)


class RequestTimeoutMiddleware(MiddlewareMixin):
    """
    请求超时监控中间件
    监控长时间运行的请求并记录日志
    """
    
    REQUEST_TIMEOUT_WARNING = 60  # 警告阈值：60秒
    REQUEST_TIMEOUT_CRITICAL = 180  # 严重阈值：180秒
    
    def process_request(self, request):
        """记录请求开始时间"""
        request.start_time = time.time()
        request.path_info = request.path  # 保存路径信息
        
    def process_response(self, request, response):
        """检查请求耗时并记录"""
        if hasattr(request, 'start_time'):
            duration = time.time() - request.start_time
            
            if duration >= self.REQUEST_TIMEOUT_CRITICAL:
                logger.critical(
                    f"[SLOW REQUEST] {request.method} {request.path} - "
                    f"Duration: {duration:.2f}s (CRITICAL)"
                )
            elif duration >= self.REQUEST_TIMEOUT_WARNING:
                logger.warning(
                    f"[SLOW REQUEST] {request.method} {request.path} - "
                    f"Duration: {duration:.2f}s (WARNING)"
                )
        
        return response
    
    def process_exception(self, request, exception):
        """记录异常请求"""
        if hasattr(request, 'start_time'):
            duration = time.time() - request.start_time
            logger.error(
                f"[REQUEST ERROR] {request.method} {request.path} - "
                f"Duration: {duration:.2f}s - Exception: {str(exception)}"
            )
        return None


def health_check(request):
    """
    健康检查端点
    
    返回服务状态信息
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "Django_xm",
        "version": "1.0.0",
        "endpoints": {
            "health": "/api/health/",
            "chat": "/api/chat/",
            "rag": "/api/rag/",
            "workflow": "/api/workflow/",
            "deep_research": "/api/deep-research/"
        },
        "database": "connected",
        "memory": {
            "status": "normal"
        }
    }
    
    return JsonResponse(health_status)


def request_monitor(request):
    """
    请求监控端点
    
    返回当前系统状态和慢请求统计
    """
    monitor_data = {
        "timestamp": datetime.now().isoformat(),
        "system": {
            "uptime": "N/A",
            "load": "N/A"
        },
        "requests": {
            "total": "N/A",
            "slow_requests": "N/A"
        },
        "endpoints": [
            {
                "path": "/api/health/",
                "description": "健康检查"
            },
            {
                "path": "/api/monitor/",
                "description": "请求监控"
            }
        ]
    }
    
    return JsonResponse(monitor_data)
