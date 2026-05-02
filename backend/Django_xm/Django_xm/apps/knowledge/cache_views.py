"""
Cache 健康检查和统计 API 视图
"""
import logging
import os
from pathlib import Path

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.db import connections
from django.conf import settings as django_settings

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
from Django_xm.apps.ai_engine.config import settings as app_cfg
from .services.index_service import IndexManager

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


def _get_mysql_status():
    """获取 MySQL 数据库状态"""
    try:
        with connections['default'].cursor() as cursor:
            # 获取版本
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0]
            
            # 获取连接数
            cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
            threads_connected = cursor.fetchone()[1]
            
            # 获取查询数
            cursor.execute("SHOW STATUS LIKE 'Questions'")
            questions = cursor.fetchone()[1]
            
            # 获取慢查询数
            cursor.execute("SHOW STATUS LIKE 'Slow_queries'")
            slow_queries = cursor.fetchone()[1]
            
            # 获取表统计
            cursor.execute("""
                SELECT 
                    COUNT(*) as table_count,
                    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) as total_size_mb
                FROM information_schema.tables 
                WHERE table_schema = DATABASE()
            """)
            table_stats = cursor.fetchone()
            table_count = table_stats[0]
            total_size_mb = table_stats[1]
            
            return {
                'backend': 'MySQL',
                'version': version,
                'connection': 'healthy',
                'threads_connected': threads_connected,
                'questions': questions,
                'slow_queries': slow_queries,
                'table_count': table_count,
                'total_size_mb': total_size_mb,
                'database_name': django_settings.DATABASES['default']['NAME'],
            }
    except Exception as e:
        logger.error(f"获取 MySQL 状态失败: {e}")
        return {
            'backend': 'MySQL',
            'connection': 'unhealthy',
            'error': str(e),
        }


def _get_vector_store_status():
    """获取向量存储状态"""
    try:
        manager = IndexManager()
        base_path = manager.base_path
        
        # 确保基础路径存在
        if not base_path.exists():
            base_path.mkdir(parents=True, exist_ok=True)
        
        # 获取所有索引
        all_indices = manager.list_indexes()
        index_count = len(all_indices)
        
        # 计算总大小
        total_size = 0
        index_info = []
        
        for idx in all_indices:
            try:
                idx_name = idx.get('name')
                if not idx_name:
                    continue
                    
                idx_path = base_path / idx_name
                idx_size = 0
                
                if idx_path.exists():
                    for root, dirs, files in os.walk(idx_path):
                        for file in files:
                            try:
                                file_path = Path(root) / file
                                idx_size += file_path.stat().st_size
                            except (OSError, Exception):
                                pass
                
                total_size += idx_size
                
                index_info.append({
                    'name': idx_name,
                    'original_name': idx.get('name', idx_name),
                    'size': idx_size,
                    'size_human': _format_size(idx_size),
                    'created_at': idx.get('created_at', ''),
                    'updated_at': idx.get('updated_at', ''),
                    'num_documents': idx.get('num_documents', 0),
                })
            except Exception as idx_err:
                logger.warning(f"处理索引 {idx.get('name')} 时出错: {idx_err}")
                continue
        
        return {
            'backend': app_cfg.vector_store_type,
            'base_path': str(base_path),
            'connection': 'healthy',
            'index_count': index_count,
            'total_size': total_size,
            'total_size_human': _format_size(total_size),
            'indices': index_info,
        }
    except Exception as e:
        logger.error(f"获取向量存储状态失败: {e}", exc_info=True)
        backend_type = 'unknown'
        try:
            backend_type = app_cfg.vector_store_type
        except (AttributeError, Exception):
            pass
        
        base_path_str = ''
        try:
            base_path_str = str(Path(app_cfg.vector_store_path))
        except (AttributeError, Exception):
            pass
            
        return {
            'backend': backend_type,
            'base_path': base_path_str,
            'connection': 'unhealthy',
            'index_count': 0,
            'total_size': 0,
            'total_size_human': '0 B',
            'indices': [],
            'error': str(e),
        }


def _format_size(size_bytes):
    """格式化字节大小为人类可读格式"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


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


class MySQLStatusView(APIView):
    """MySQL 数据库状态视图"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            cache_key = "status:mysql"
            cached = CacheService.get(cache_key)
            if cached is not None:
                return success_response(data=cached)

            status_info = _get_mysql_status()
            CacheService.set(cache_key, status_info, ttl=30)
            return success_response(data=status_info)
        except Exception as e:
            logger.error(f"获取 MySQL 状态失败: {e}", exc_info=True)
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

            status_info = _get_vector_store_status()
            CacheService.set(cache_key, status_info, ttl=30)
            return success_response(data=status_info)
        except Exception as e:
            logger.error(f"获取向量存储状态失败: {e}", exc_info=True)
            return error_response(code=ErrorCode.SERVER_ERROR, message=str(e))


class DatabaseOverviewView(APIView):
    """数据库总览视图 - 包含 MySQL、向量存储和 Redis"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            cache_key = "status:database_overview"
            cached = CacheService.get(cache_key)
            if cached is not None:
                return success_response(data=cached)

            mysql_status = _get_mysql_status()
            vector_status = _get_vector_store_status()
            redis_info = _get_redis_info()
            
            redis_status = {
                'backend': 'Redis',
                'connection': 'healthy' if redis_info else 'unhealthy',
            }
            if redis_info:
                redis_status['version'] = redis_info.get('redis_version', '-')
                redis_status['used_memory_human'] = redis_info.get('used_memory_human', '-')
                redis_status['connected_clients'] = redis_info.get('connected_clients', 0)
                db_info = redis_info.get('db0', {})
                if isinstance(db_info, dict):
                    redis_status['total_keys'] = db_info.get('keys', 0)
            
            overview = {
                'mysql': mysql_status,
                'vector_store': vector_status,
                'redis': redis_status,
            }
            CacheService.set(cache_key, overview, ttl=30)
            return success_response(data=overview)
        except Exception as e:
            logger.error(f"获取数据库总览失败: {e}", exc_info=True)
            return error_response(code=ErrorCode.SERVER_ERROR, message=str(e))
