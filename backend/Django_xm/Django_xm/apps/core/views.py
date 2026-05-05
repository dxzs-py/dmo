"""
核心视图模块

提供健康检查、请求监控等基础设施视图。
异常处理器已迁移至 Django_xm.apps.common.exceptions。
"""

import logging
import time

from django.http import JsonResponse
from rest_framework import status as http_status

from Django_xm.apps.common.exceptions import custom_exception_handler  # noqa: F401

logger = logging.getLogger(__name__)


def health_check(request):
    """健康检查接口"""
    return JsonResponse({
        'status': 'healthy',
        'timestamp': time.time(),
    })


def request_monitor(request):
    """请求监控视图"""
    if request.method == 'OPTIONS':
        return JsonResponse({}, status=http_status.HTTP_200_OK)
    return JsonResponse({'message': 'request_monitor'})
