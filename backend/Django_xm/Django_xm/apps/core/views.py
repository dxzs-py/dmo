"""
核心视图模块

提供健康检查、数据库监控等基础设施视图。
异常处理器已迁移至 Django_xm.common.exceptions。

依赖说明（Task 15.3 / 15.5）：
    本视图原导入 ``cache_manager.services.cache_service.CacheService`` 用于缓存，
    违反 ``core → cache_manager`` 分层。现改用 Django 内置 ``django.core.cache``，
    缓存配置由 ``settings.CACHES`` 统一管理，无需依赖 ``cache_manager`` app。

    PostgreSQL / VectorStore / Redis 的细粒度状态端点已于 2026-10-01 删除：
    ``DatabaseOverviewView``（/database/overview/）聚合的 ``overview``
    已完整包含 postgresql 状态与全部注册提供者（vector_store / redis），
    细粒度端点属冗余子集且前后端均零调用方。
"""

from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.services.db_monitor import DatabaseMonitor
from Django_xm.common.responses import success_response
from Django_xm.common.serializers import EmptySerializer


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


class DatabaseOverviewView(APIView):
    """数据库总览视图（PostgreSQL + VectorStore + Redis）

    通过 ``DatabaseMonitor.get_database_overview()`` 聚合所有已注册状态提供者。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        cache_key = "status:database_overview"
        cached = cache.get(cache_key)
        if cached is not None:
            return success_response(data=cached)

        overview = DatabaseMonitor.get_database_overview()
        cache.set(cache_key, overview, 30)
        return success_response(data=overview)
