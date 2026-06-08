"""
核心视图模块

提供健康检查、请求监控、数据库监控等基础设施视图。
异常处理器已迁移至 Django_xm.common.exceptions。
"""

import logging
import time

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework import status as drf_status

from Django_xm.common.permissions import IsAdmin
from Django_xm.common.responses import success_response, error_response
from Django_xm.common.error_codes import ErrorCode
from Django_xm.apps.core.services.db_monitor import DatabaseMonitor
from Django_xm.apps.cache_manager.services.cache_service import CacheService

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """健康检查端点"""
    from django.db import connection
    from django.core.cache import cache

    checks = {}
    # 数据库检查
    try:
        connection.ensure_connection()
        checks['database'] = 'ok'
    except Exception:
        checks['database'] = 'error'

    # 缓存检查
    try:
        cache.set('health_check', 'ok', 1)
        checks['cache'] = 'ok' if cache.get('health_check') == 'ok' else 'error'
    except Exception:
        checks['cache'] = 'error'

    return success_response(data=checks)


@api_view(['GET'])
@permission_classes([IsAdmin])
def request_monitor(request):
    """请求监控端点（仅管理员）"""
    monitor_data = {
        'message': 'request_monitor',
        'timestamp': time.time(),
    }
    return success_response(data=monitor_data)


class PostgreSQLStatusView(APIView):
    """PostgreSQL 数据库状态视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            cache_key = "status:postgresql"
            cached = CacheService.get(cache_key)
            if cached is not None:
                return success_response(data=cached)

            status_info = DatabaseMonitor.get_postgresql_status()
            CacheService.set(cache_key, status_info, ttl=30)
            return success_response(data=status_info)
        except Exception as e:
            logger.error(f"获取 PostgreSQL 状态失败: {e}", exc_info=True)
            return error_response(code=ErrorCode.SERVER_ERROR, message=str(e))


class VectorStoreStatusView(APIView):
    """向量存储状态视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            cache_key = "status:vector_store"
            cached = CacheService.get(cache_key)
            if cached is not None:
                return success_response(data=cached)

            status_info = DatabaseMonitor.get_vector_store_status()
            CacheService.set(cache_key, status_info, ttl=30)
            return success_response(data=status_info)
        except Exception as e:
            logger.error(f"获取向量存储状态失败: {e}", exc_info=True)
            return error_response(code=ErrorCode.SERVER_ERROR, message=str(e))


class DatabaseOverviewView(APIView):
    """数据库总览视图（PostgreSQL + VectorStore + Redis）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            cache_key = "status:database_overview"
            cached = CacheService.get(cache_key)
            if cached is not None:
                return success_response(data=cached)

            overview = DatabaseMonitor.get_database_overview()
            CacheService.set(cache_key, overview, ttl=30)
            return success_response(data=overview)
        except Exception as e:
            logger.error(f"获取数据库总览失败: {e}", exc_info=True)
            return error_response(code=ErrorCode.SERVER_ERROR, message=str(e))
