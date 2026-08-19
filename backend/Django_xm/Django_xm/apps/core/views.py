"""
核心视图模块

提供健康检查、数据库监控等基础设施视图。
异常处理器已迁移至 Django_xm.common.exceptions。

依赖说明（Task 15.3 / 15.5）：
    本视图原导入 ``cache_manager.services.cache_service.CacheService`` 用于缓存，
    违反 ``core → cache_manager`` 分层。现改用 Django 内置 ``django.core.cache``，
    缓存配置由 ``settings.CACHES`` 统一管理，无需依赖 ``cache_manager`` app。

    向量存储状态原调用 ``DatabaseMonitor.get_vector_store_status()``（依赖 knowledge），
    现通过 ``status_registry`` 按名查询，由 ``knowledge`` 注册的提供者实现。
"""

import logging

from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.services.db_monitor import DatabaseMonitor
from Django_xm.apps.core.services.status_registry import get_status_by_name
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import error_response, success_response
from Django_xm.common.serializers import EmptySerializer

logger = logging.getLogger(__name__)


@extend_schema(responses={200: EmptySerializer})
@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """健康检查端点"""
    from django.db import connection

    checks = {}
    # 数据库检查
    try:
        connection.ensure_connection()
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    # 缓存检查
    try:
        cache.set("health_check", "ok", 1)
        checks["cache"] = "ok" if cache.get("health_check") == "ok" else "error"
    except Exception:
        checks["cache"] = "error"

    return success_response(data=checks)


class PostgreSQLStatusView(APIView):
    """PostgreSQL 数据库状态视图"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            cache_key = "status:postgresql"
            cached = cache.get(cache_key)
            if cached is not None:
                return success_response(data=cached)

            status_info = DatabaseMonitor.get_postgresql_status()
            cache.set(cache_key, status_info, 30)
            return success_response(data=status_info)
        except Exception:
            logger.exception("获取 PostgreSQL 状态失败")
            return error_response(code=ErrorCode.SERVER_ERROR, message="获取 PostgreSQL 状态失败")


class VectorStoreStatusView(APIView):
    """向量存储状态视图

    通过 ``status_registry`` 查询 ``knowledge`` 注册的 ``VectorStoreStatusProvider``。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            cache_key = "status:vector_store"
            cached = cache.get(cache_key)
            if cached is not None:
                return success_response(data=cached)

            # 通过注册表查询向量存储状态（Task 15.3）
            status_info = get_status_by_name("vector_store")
            cache.set(cache_key, status_info, 30)
            return success_response(data=status_info)
        except Exception:
            logger.exception("获取向量存储状态失败")
            return error_response(code=ErrorCode.SERVER_ERROR, message="获取向量存储状态失败")


class DatabaseOverviewView(APIView):
    """数据库总览视图（PostgreSQL + VectorStore + Redis）

    通过 ``DatabaseMonitor.get_database_overview()`` 聚合所有已注册状态提供者。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            cache_key = "status:database_overview"
            cached = cache.get(cache_key)
            if cached is not None:
                return success_response(data=cached)

            overview = DatabaseMonitor.get_database_overview()
            cache.set(cache_key, overview, 30)
            return success_response(data=overview)
        except Exception:
            logger.exception("获取数据库总览失败")
            return error_response(code=ErrorCode.SERVER_ERROR, message="获取数据库总览失败")
