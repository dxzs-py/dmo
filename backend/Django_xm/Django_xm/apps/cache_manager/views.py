import logging

from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from Django_xm.common.responses import success_response, error_response
from Django_xm.common.error_codes import ErrorCode
from Django_xm.apps.cache_manager.services.cache_service import (
    CacheService,
    CacheHealthChecker,
    CacheInvalidationStrategy,
    RedisDirectClient,
    ModelResponseCacheService,
    CACHE_PREFIX_QUERY,
    CACHE_PREFIX_MODEL,
    CACHE_PREFIX_EMBEDDING,
    CACHE_PREFIX_TOOL,
    CACHE_PREFIX_VECTOR,
    CACHE_PREFIX_SESSION,
    CACHE_PREFIX_AGENT,
    CACHE_PREFIX_LABELS,
    get_redis_client,
    get_redis_info,
)
from Django_xm.apps.ai_engine.config import settings as app_cfg

logger = logging.getLogger(__name__)


def _count_keys_by_prefix(prefix):
    client = get_redis_client()
    if client is None:
        return 0
    try:
        count = 0
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, match=f"{prefix}:*", count=500)
            count += len(keys)
            if cursor == 0:
                break
        return count
    except Exception as e:
        logger.error(f"扫描缓存键失败: {e}")
        return 0


class CacheHealthView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            health_info = CacheHealthChecker.get_health_info()

            knowledge_redis_info = get_redis_info()
            if knowledge_redis_info:
                health_info['backend'] = 'Redis'
                health_info['version'] = knowledge_redis_info.get('redis_version', '-')
                health_info['used_memory_human'] = knowledge_redis_info.get('used_memory_human', '-')
                health_info['connected_clients'] = knowledge_redis_info.get('connected_clients', 0)
                health_info['uptime_in_seconds'] = knowledge_redis_info.get('uptime_in_seconds', 0)
                health_info['total_keys'] = knowledge_redis_info.get('db0', {}).get('keys', 0) if isinstance(knowledge_redis_info.get('db0'), dict) else 0

            redis_direct_info = RedisDirectClient.get_info()
            if redis_direct_info:
                health_info.setdefault('used_memory_human', redis_direct_info.get('used_memory_human', '-'))
                health_info.setdefault('connected_clients', redis_direct_info.get('connected_clients', 0))
                health_info['db_size'] = redis_direct_info.get('db_size', 0)
                health_info['total_commands_processed'] = redis_direct_info.get('total_commands_processed', 0)
                health_info['keyspace_hits'] = redis_direct_info.get('keyspace_hits', 0)
                health_info['keyspace_misses'] = redis_direct_info.get('keyspace_misses', 0)

            is_healthy = health_info.get('connection') == 'healthy'
            return success_response(
                data=health_info,
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
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            stats = CacheService.get_stats()

            knowledge_redis_info = get_redis_info()
            if knowledge_redis_info:
                db_info = knowledge_redis_info.get('db0', {})
                if isinstance(db_info, dict):
                    stats['total_keys'] = db_info.get('keys', 0)
                    stats['expires'] = db_info.get('expires', 0)
                    stats['avg_ttl'] = db_info.get('avg_ttl', 0)
                stats['used_memory'] = knowledge_redis_info.get('used_memory', 0)
                stats['used_memory_human'] = knowledge_redis_info.get('used_memory_human', '-')
                stats['peak_memory_human'] = knowledge_redis_info.get('used_memory_peak_human', '-')
                stats['total_commands_processed'] = knowledge_redis_info.get('total_commands_processed', 0)
                stats['keyspace_hits'] = knowledge_redis_info.get('keyspace_hits', 0)
                stats['keyspace_misses'] = knowledge_redis_info.get('keyspace_misses', 0)
                ks_total = stats['keyspace_hits'] + stats['keyspace_misses']
                stats['redis_hit_rate'] = round(stats['keyspace_hits'] / ks_total * 100, 2) if ks_total > 0 else 0

            category_stats = []
            for prefix, label in CACHE_PREFIX_LABELS.items():
                count = _count_keys_by_prefix(prefix)
                category_stats.append({
                    'prefix': prefix,
                    'label': label,
                    'count': count,
                })
            stats['categories'] = category_stats

            redis_direct_info = RedisDirectClient.get_info()
            if redis_direct_info:
                stats.setdefault('used_memory_human', redis_direct_info.get('used_memory_human', '-'))
                stats.setdefault('redis_hit_rate', 0)
                stats['db_size'] = redis_direct_info.get('db_size', 0)

            return success_response(data=stats)
        except Exception as e:
            logger.error(f"获取缓存统计失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CacheInvalidateView(APIView):
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


class CacheClearView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            pattern = request.data.get('pattern')
            cache_type = request.data.get('type', 'all')

            cleared = 0
            if pattern:
                CacheService.delete_pattern(pattern)
                cleared = 1
            elif cache_type == 'query':
                user = request.user
                CacheService.delete_pattern(f"rag_query:user_{user.id}_*")
            elif cache_type == 'model':
                ModelResponseCacheService.invalidate_model_cache(app_cfg.get_openai_config()['model'])
            else:
                client = get_redis_client()
                if client:
                    for prefix in CACHE_PREFIX_LABELS:
                        cursor = 0
                        while True:
                            cursor, keys = client.scan(cursor, match=f"{prefix}:*", count=500)
                            if keys:
                                client.delete(*keys)
                                cleared += len(keys)
                            if cursor == 0:
                                break
                CacheService.reset_stats()

            return success_response(data={'cleared': cleared}, message=f'已清除 {cleared} 个缓存键')
        except Exception as e:
            logger.error(f"清除缓存失败: {e}", exc_info=True)
            return error_response(code=ErrorCode.SERVER_ERROR, message=str(e))


class CacheResetStatsView(APIView):
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
