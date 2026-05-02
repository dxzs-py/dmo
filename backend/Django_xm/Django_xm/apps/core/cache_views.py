"""
缓存管理 API 视图
提供缓存健康检查、统计、失效等管理端点
"""
import logging

from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from Django_xm.apps.common.responses import success_response, error_response
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.ai_engine.services.cache_service import (
    CacheService,
    CacheHealthChecker,
    CacheInvalidationStrategy,
    RedisDirectClient,
)

logger = logging.getLogger(__name__)


class CacheHealthView(APIView):
    """缓存健康检查视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            health_info = CacheHealthChecker.get_health_info()
            redis_info = RedisDirectClient.get_info()

            data = {
                'cache': health_info,
                'redis': redis_info,
            }

            is_healthy = health_info.get('connection') == 'healthy'
            return success_response(
                data=data,
                message='缓存服务正常' if is_healthy else '缓存服务异常',
            )
        except Exception as e:
            logger.error(f"缓存健康检查失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CacheStatsView(APIView):
    """缓存统计视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            stats = CacheService.get_stats()
            redis_info = RedisDirectClient.get_info()

            data = {
                'hit_stats': stats,
                'redis_info': redis_info,
            }

            return success_response(data=data)
        except Exception as e:
            logger.error(f"获取缓存统计失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CacheInvalidateView(APIView):
    """缓存失效视图"""
    permission_classes = [IsAdminUser]

    def post(self, request):
        try:
            scope = request.data.get('scope', 'all')
            index_name = request.data.get('index_name')
            session_id = request.data.get('session_id')
            pattern = request.data.get('pattern')

            if scope == 'index' and index_name:
                CacheInvalidationStrategy.on_index_updated(index_name)
                message = f'索引 {index_name} 缓存已失效'
            elif scope == 'session' and session_id:
                CacheInvalidationStrategy.on_session_updated(session_id)
                message = f'会话 {session_id} 缓存已失效'
            elif scope == 'pattern' and pattern:
                count = RedisDirectClient.delete_keys_by_pattern(pattern)
                message = f'模式 {pattern} 缓存已失效，删除 {count} 个 key'
            elif scope == 'all':
                results = CacheInvalidationStrategy.invalidate_all()
                message = f'全量缓存已失效: {results}'
            else:
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED,
                    message='请指定有效的 scope (all/index/session/pattern) 及对应参数',
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

            return success_response(message=message)
        except Exception as e:
            logger.error(f"缓存失效操作失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CacheResetStatsView(APIView):
    """重置缓存统计视图"""
    permission_classes = [IsAdminUser]

    def post(self, request):
        try:
            CacheService.reset_stats()
            return success_response(message='缓存统计已重置')
        except Exception as e:
            logger.error(f"重置缓存统计失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
