"""
Cache 健康检查和统计 API 视图
"""
import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.common.responses import success_response, error_response
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.ai_engine.services.cache_service import (
    CacheService,
    CacheHealthChecker,
    QueryCacheService,
    ModelResponseCacheService,
    CACHE_PREFIX_QUERY,
    CACHE_PREFIX_MODEL,
    CACHE_PREFIX_EMBEDDING,
    CACHE_PREFIX_TOOL,
    CACHE_PREFIX_VECTOR,
    CACHE_PREFIX_SESSION,
    CACHE_PREFIX_AGENT,
)

logger = logging.getLogger(__name__)

CACHE_PREFIX_MAP = {
    'rag_query': 'RAG 查询缓存',
    'model_response': '模型响应缓存',
    'embedding': 'Embedding 缓存',
    'tool_result': '工具结果缓存',
    'vector_search': '向量检索缓存',
    'chat_session': '会话缓存',
    'agent_state': 'Agent 状态缓存',
}


def _get_redis_client():
    from django.core.cache import caches
    default_cache = caches['default']
    if hasattr(default_cache, 'client'):
        client = default_cache.client
        if hasattr(client, 'get_client'):
            return client.get_client()
        return client
    return None


def _get_redis_info():
    client = _get_redis_client()
    if client is None:
        return None
    try:
        info = client.info()
        return info
    except Exception as e:
        logger.error(f"获取 Redis info 失败: {e}")
        return None


def _count_keys_by_prefix(prefix):
    client = _get_redis_client()
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


class CacheHealthCheckView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            health_info = CacheHealthChecker.get_health_info()
            redis_info = _get_redis_info()
            if redis_info:
                health_info['backend'] = 'Redis'
                health_info['version'] = redis_info.get('redis_version', '-')
                health_info['used_memory_human'] = redis_info.get('used_memory_human', '-')
                health_info['connected_clients'] = redis_info.get('connected_clients', 0)
                health_info['uptime_in_seconds'] = redis_info.get('uptime_in_seconds', 0)
                health_info['total_keys'] = redis_info.get('db0', {}).get('keys', 0) if isinstance(redis_info.get('db0'), dict) else 0
            return success_response(data=health_info)
        except Exception as e:
            logger.error(f"缓存健康检查失败: {e}", exc_info=True)
            return error_response(code=ErrorCode.SERVER_ERROR, message=str(e))


class CacheStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            stats = CacheService.get_stats()
            redis_info = _get_redis_info()
            if redis_info:
                db_info = redis_info.get('db0', {})
                if isinstance(db_info, dict):
                    stats['total_keys'] = db_info.get('keys', 0)
                    stats['expires'] = db_info.get('expires', 0)
                    stats['avg_ttl'] = db_info.get('avg_ttl', 0)
                stats['used_memory'] = redis_info.get('used_memory', 0)
                stats['used_memory_human'] = redis_info.get('used_memory_human', '-')
                stats['peak_memory_human'] = redis_info.get('used_memory_peak_human', '-')
                stats['total_commands_processed'] = redis_info.get('total_commands_processed', 0)
                stats['keyspace_hits'] = redis_info.get('keyspace_hits', 0)
                stats['keyspace_misses'] = redis_info.get('keyspace_misses', 0)
                ks_total = stats['keyspace_hits'] + stats['keyspace_misses']
                stats['redis_hit_rate'] = round(stats['keyspace_hits'] / ks_total * 100, 2) if ks_total > 0 else 0

            category_stats = []
            for prefix, label in CACHE_PREFIX_MAP.items():
                count = _count_keys_by_prefix(prefix)
                category_stats.append({
                    'prefix': prefix,
                    'label': label,
                    'count': count,
                })
            stats['categories'] = category_stats

            return success_response(data=stats)
        except Exception as e:
            logger.error(f"获取缓存统计失败: {e}", exc_info=True)
            return error_response(code=ErrorCode.SERVER_ERROR, message=str(e))


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
                from Django_xm.apps.ai_engine.config import settings as app_cfg
                ModelResponseCacheService.invalidate_model_cache(app_cfg.get_openai_config()['model'])
            else:
                client = _get_redis_client()
                if client:
                    for prefix in CACHE_PREFIX_MAP:
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
