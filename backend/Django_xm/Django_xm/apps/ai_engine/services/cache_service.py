"""
Redis 缓存服务 - 通用缓存增强模块

提供以下缓存策略：
1. 热点查询缓存：缓存 RAG 检索结果、常用问答
2. 模型响应缓存：缓存相同输入的 AI 回复
3. 向量检索缓存：缓存 embedding 和相似度搜索结果
4. 工具调用缓存：缓存工具执行结果（如天气、搜索）
5. 会话元数据缓存：扩展 SecureSessionCacheService

设计原则：
- 使用前缀命名空间避免 key 冲突
- 支持可配置的 TTL
- 支持缓存失效通知
- 统计缓存命中率
"""
import json
import hashlib
import logging
import time
from typing import Any, Optional, Dict, List, Union
from functools import wraps

from django.core.cache import cache
from django.conf import settings as django_settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ==================== 缓存前缀常量 ====================

CACHE_PREFIX_QUERY = 'rag_query'
CACHE_PREFIX_MODEL = 'model_response'
CACHE_PREFIX_EMBEDDING = 'embedding'
CACHE_PREFIX_TOOL = 'tool_result'
CACHE_PREFIX_VECTOR = 'vector_search'
CACHE_PREFIX_INDEX = 'index_meta'
CACHE_PREFIX_CONFIG = 'app_config'
CACHE_PREFIX_SESSION = 'chat_session'
CACHE_PREFIX_AGENT = 'agent_state'


# ==================== 缓存 TTL 配置 ====================

class CacheTTL:
    """缓存过期时间配置（秒）"""
    QUERY_SHORT = 300
    QUERY_MEDIUM = 1800
    QUERY_LONG = 86400
    MODEL_SHORT = 600
    MODEL_MEDIUM = 3600
    EMBEDDING = 604800
    TOOL_SHORT = 300
    TOOL_MEDIUM = 3600
    TOOL_LONG = 86400
    VECTOR_SEARCH = 3600
    INDEX_META = 300
    SESSION_SHORT = 1800
    SESSION_MEDIUM = 7200
    SESSION_LONG = 86400
    AGENT_STATE = 3600


# ==================== 通用缓存工具类 ====================

class CacheService:
    """
    通用 Redis 缓存服务
    
    提供统一的缓存操作接口，支持：
    - 类型安全的存取
    - 自动序列化/反序列化
    - 缓存统计
    """
    
    _hit_count = 0
    _miss_count = 0
    
    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """
        获取缓存值
        
        Args:
            key: 缓存键
            default: 默认值
            
        Returns:
            缓存值或默认值
        """
        try:
            value = cache.get(key, default)
            if value is not None and value != default:
                cls._hit_count += 1
                logger.debug(f"缓存命中: {key}")
            else:
                cls._miss_count += 1
                logger.debug(f"缓存未命中: {key}")
            return value
        except Exception as e:
            logger.error(f"缓存读取失败: {key}, 错误: {e}")
            cls._miss_count += 1
            return default
    
    @classmethod
    def set(cls, key: str, value: Any, ttl: int = CacheTTL.QUERY_MEDIUM) -> bool:
        """
        设置缓存值
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒）
            
        Returns:
            是否设置成功
        """
        try:
            cache.set(key, value, timeout=ttl)
            logger.debug(f"缓存设置: {key}, TTL={ttl}s")
            return True
        except Exception as e:
            logger.error(f"缓存设置失败: {key}, 错误: {e}")
            return False
    
    @classmethod
    def delete(cls, key: str) -> bool:
        """删除缓存"""
        try:
            cache.delete(key)
            logger.debug(f"缓存删除: {key}")
            return True
        except Exception as e:
            logger.error(f"缓存删除失败: {key}, 错误: {e}")
            return False
    
    @classmethod
    def delete_pattern(cls, pattern: str) -> int:
        """
        按模式删除缓存
        
        Args:
            pattern: 匹配模式，如 'rag_query:*'
            
        Returns:
            删除的数量
        """
        try:
            from django.core.cache import caches
            default_cache = caches['default']
            
            if hasattr(default_cache, 'delete_pattern'):
                default_cache.delete_pattern(pattern)
                logger.info(f"缓存模式删除: {pattern}")
                return 1
            return 0
        except Exception as e:
            logger.error(f"缓存模式删除失败: {pattern}, 错误: {e}")
            return 0
    
    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total = cls._hit_count + cls._miss_count
        hit_rate = (cls._hit_count / total * 100) if total > 0 else 0
        return {
            'hit_count': cls._hit_count,
            'miss_count': cls._miss_count,
            'total_requests': total,
            'hit_rate': round(hit_rate, 2),
        }
    
    @classmethod
    def reset_stats(cls):
        """重置统计信息"""
        cls._hit_count = 0
        cls._miss_count = 0


# ==================== 缓存键生成器 ====================

def generate_query_cache_key(query: str, index_name: str, k: int = 4) -> str:
    """
    生成 RAG 查询缓存键
    
    Args:
        query: 查询文本
        index_name: 索引名称
        k: 检索文档数
        
    Returns:
        缓存键
    """
    query_hash = hashlib.md5(query.encode('utf-8')).hexdigest()[:12]
    return f"{CACHE_PREFIX_QUERY}:{index_name}:{query_hash}:k{k}"


def generate_model_cache_key(
    prompt: str,
    model_name: str,
    mode: str = 'default',
    tools_hash: Optional[str] = None
) -> str:
    """
    生成模型响应缓存键
    
    Args:
        prompt: 提示词
        model_name: 模型名称
        mode: 模式
        tools_hash: 工具列表哈希
        
    Returns:
        缓存键
    """
    prompt_hash = hashlib.md5(prompt.encode('utf-8')).hexdigest()[:12]
    tools_part = f":t{tools_hash}" if tools_hash else ''
    return f"{CACHE_PREFIX_MODEL}:{model_name}:{mode}:{prompt_hash}{tools_part}"


def generate_embedding_cache_key(text: str, model: str = 'default') -> str:
    """生成 embedding 缓存键"""
    text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()[:12]
    return f"{CACHE_PREFIX_EMBEDDING}:{model}:{text_hash}"


def generate_tool_cache_key(
    tool_name: str,
    params: Dict[str, Any],
    user_id: Optional[int] = None
) -> str:
    """
    生成工具调用缓存键
    
    Args:
        tool_name: 工具名称
        params: 工具参数
        user_id: 用户ID（可选）
        
    Returns:
        缓存键
    """
    params_str = json.dumps(params, sort_keys=True, ensure_ascii=False)
    params_hash = hashlib.md5(params_str.encode('utf-8')).hexdigest()[:12]
    user_part = f":u{user_id}" if user_id else ''
    return f"{CACHE_PREFIX_TOOL}:{tool_name}:{params_hash}{user_part}"


def generate_vector_search_cache_key(
    query: str,
    index_name: str,
    k: int = 4,
    score_threshold: Optional[float] = None
) -> str:
    """生成向量搜索缓存键"""
    query_hash = hashlib.md5(query.encode('utf-8')).hexdigest()[:12]
    threshold_part = f":th{score_threshold}" if score_threshold else ''
    return f"{CACHE_PREFIX_VECTOR}:{index_name}:{query_hash}:k{k}{threshold_part}"


# ==================== 专用缓存服务 ====================

class QueryCacheService:
    """
    热点查询缓存服务
    
    缓存 RAG 检索结果和常用问答，避免重复检索和生成
    """
    
    @classmethod
    def get_cached_query(cls, query: str, index_name: str, k: int = 4) -> Optional[Dict]:
        """获取缓存的查询结果"""
        key = generate_query_cache_key(query, index_name, k)
        return CacheService.get(key)
    
    @classmethod
    def cache_query_result(
        cls,
        query: str,
        result: Dict,
        index_name: str,
        k: int = 4,
        ttl: int = CacheTTL.QUERY_MEDIUM
    ) -> bool:
        """缓存查询结果"""
        key = generate_query_cache_key(query, index_name, k)
        
        cache_data = {
            'query': query,
            'result': result,
            'cached_at': timezone.now().isoformat(),
            'index': index_name,
        }
        
        return CacheService.set(key, cache_data, ttl)
    
    @classmethod
    def invalidate_index_queries(cls, index_name: str) -> bool:
        """使某个索引的所有查询缓存失效"""
        pattern = f"{CACHE_PREFIX_QUERY}:{index_name}:*"
        CacheService.delete_pattern(pattern)
        logger.info(f"索引查询缓存已失效: {index_name}")
        return True


class ModelResponseCacheService:
    """
    模型响应缓存服务
    
    缓存相同输入的 AI 回复，减少 API 调用
    """
    
    @classmethod
    def get_cached_response(
        cls,
        prompt: str,
        model_name: str,
        mode: str = 'default'
    ) -> Optional[Dict]:
        """获取缓存的模型响应"""
        key = generate_model_cache_key(prompt, model_name, mode)
        return CacheService.get(key)
    
    @classmethod
    def cache_model_response(
        cls,
        prompt: str,
        response: Dict,
        model_name: str,
        mode: str = 'default',
        ttl: int = CacheTTL.MODEL_MEDIUM
    ) -> bool:
        """缓存模型响应"""
        key = generate_model_cache_key(prompt, model_name, mode)
        
        cache_data = {
            'prompt': prompt,
            'response': response,
            'model': model_name,
            'mode': mode,
            'cached_at': timezone.now().isoformat(),
        }
        
        return CacheService.set(key, cache_data, ttl)
    
    @classmethod
    def invalidate_model_cache(cls, model_name: str) -> bool:
        """使某个模型的所有缓存失效"""
        pattern = f"{CACHE_PREFIX_MODEL}:{model_name}:*"
        CacheService.delete_pattern(pattern)
        logger.info(f"模型缓存已失效: {model_name}")
        return True


class ToolResultCacheService:
    """
    工具调用结果缓存服务
    
    缓存工具执行结果，减少重复调用
    """
    
    @classmethod
    def get_cached_tool_result(
        cls,
        tool_name: str,
        params: Dict[str, Any],
        user_id: Optional[int] = None
    ) -> Optional[Any]:
        """获取缓存的工具结果"""
        key = generate_tool_cache_key(tool_name, params, user_id)
        return CacheService.get(key)
    
    @classmethod
    def cache_tool_result(
        cls,
        tool_name: str,
        params: Dict[str, Any],
        result: Any,
        user_id: Optional[int] = None,
        ttl: int = CacheTTL.TOOL_MEDIUM
    ) -> bool:
        """缓存工具结果"""
        key = generate_tool_cache_key(tool_name, params, user_id)
        return CacheService.set(key, result, ttl)


class VectorSearchCacheService:
    """
    向量搜索缓存服务
    
    缓存 embedding 计算和相似度搜索结果
    """
    
    @classmethod
    def get_cached_search(cls, query: str, index_name: str, k: int = 4) -> Optional[List]:
        """获取缓存的向量搜索结果"""
        key = generate_vector_search_cache_key(query, index_name, k)
        return CacheService.get(key)
    
    @classmethod
    def cache_search_result(
        cls,
        query: str,
        results: List,
        index_name: str,
        k: int = 4,
        ttl: int = CacheTTL.VECTOR_SEARCH
    ) -> bool:
        """缓存向量搜索结果"""
        key = generate_vector_search_cache_key(query, index_name, k)
        return CacheService.set(key, results, ttl)
    
    @classmethod
    def get_cached_embedding(cls, text: str, model: str = 'default') -> Optional[List[float]]:
        """获取缓存的 embedding"""
        key = generate_embedding_cache_key(text, model)
        return CacheService.get(key)
    
    @classmethod
    def cache_embedding(
        cls,
        text: str,
        embedding: List[float],
        model: str = 'default',
        ttl: int = CacheTTL.EMBEDDING
    ) -> bool:
        """缓存 embedding"""
        key = generate_embedding_cache_key(text, model)
        return CacheService.set(key, embedding, ttl)

    @classmethod
    def invalidate_index_queries(cls, index_name: str) -> int:
        """清除指定索引的所有向量搜索缓存"""
        pattern = f"{CACHE_PREFIX_VECTOR}:{index_name}:*"
        count = CacheService.delete_pattern(pattern)
        logger.info(f"向量搜索缓存已失效: {index_name}, 清除 {count} 个键")
        return count


# ==================== 缓存装饰器 ====================

def cache_result(
    key_generator,
    ttl: int = CacheTTL.QUERY_MEDIUM,
    cache_class=CacheService
):
    """
    缓存装饰器 - 用于缓存函数返回值
    
    Args:
        key_generator: 缓存键生成函数
        ttl: 过期时间
        cache_class: 缓存服务类
        
    Example:
        @cache_result(
            key_generator=lambda args, kwargs: f"my_cache:{args[0]}",
            ttl=3600
        )
        def expensive_function(query: str):
            return compute_result(query)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = key_generator(args, kwargs)
            
            cached = cache_class.get(cache_key)
            if cached is not None:
                logger.debug(f"装饰器缓存命中: {func.__name__}:{cache_key}")
                return cached
            
            result = func(*args, **kwargs)
            
            cache_class.set(cache_key, result, ttl)
            logger.debug(f"装饰器缓存设置: {func.__name__}:{cache_key}")
            
            return result
        return wrapper
    return decorator


def invalidate_cache(pattern: str):
    """
    缓存失效装饰器 - 调用函数时使匹配模式的缓存失效
    
    Args:
        pattern: 匹配模式
        
    Example:
        @invalidate_cache("rag_query:test_index:*")
        def rebuild_index(index_name: str):
            rebuild(index_name)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            CacheService.delete_pattern(pattern)
            logger.info(f"装饰器缓存失效: {pattern}")
            return result
        return wrapper
    return decorator


# ==================== 缓存预热 ====================

class CacheWarmer:
    """
    缓存预热服务
    
    在系统启动或低峰期预热常用数据
    """
    
    @classmethod
    def warm_query_cache(cls, queries: List[Dict[str, Any]]) -> int:
        """
        预热查询缓存
        
        Args:
            queries: 查询列表，每个元素包含：
                - query: 查询文本
                - index_name: 索引名称
                - result: 查询结果
                
        Returns:
            预热成功数量
        """
        count = 0
        for q in queries:
            try:
                QueryCacheService.cache_query_result(
                    query=q['query'],
                    result=q['result'],
                    index_name=q['index_name'],
                    ttl=CacheTTL.QUERY_LONG
                )
                count += 1
            except Exception as e:
                logger.warning(f"预热查询失败: {e}")
        return count
    
    @classmethod
    def warm_config_cache(cls, config_data: Dict[str, Any], ttl: int = CacheTTL.QUERY_LONG) -> bool:
        """
        预热配置缓存
        
        Args:
            config_data: 配置数据
            ttl: 过期时间
            
        Returns:
            是否成功
        """
        return CacheService.set(f"{CACHE_PREFIX_CONFIG}:app", config_data, ttl)


# ==================== 缓存健康检查 ====================

class CacheHealthChecker:
    """缓存健康检查"""
    
    @classmethod
    def check_connection(cls) -> bool:
        try:
            cache.set('health_check', 'ok', timeout=10)
            result = cache.get('health_check')
            return result == 'ok'
        except Exception as e:
            logger.error(f"Redis 连接检查失败: {e}")
            return False
    
    @classmethod
    def get_health_info(cls) -> Dict[str, Any]:
        connection_ok = cls.check_connection()
        stats = CacheService.get_stats()
        
        return {
            'connection': 'healthy' if connection_ok else 'unhealthy',
            'stats': stats,
            'checked_at': timezone.now().isoformat(),
        }


# ==================== 会话缓存服务 ====================

class SessionCacheService:
    """
    聊天会话缓存服务

    将聊天历史和会话元数据缓存到 Redis，
    减少数据库查询，加速会话加载
    """

    @classmethod
    def _get_session_key(cls, session_id: str) -> str:
        return f"{CACHE_PREFIX_SESSION}:meta:{session_id}"

    @classmethod
    def _get_history_key(cls, session_id: str) -> str:
        return f"{CACHE_PREFIX_SESSION}:history:{session_id}"

    @classmethod
    def get_session_meta(cls, session_id: str) -> Optional[Dict]:
        key = cls._get_session_key(session_id)
        return CacheService.get(key)

    @classmethod
    def cache_session_meta(
        cls,
        session_id: str,
        meta: Dict,
        ttl: int = CacheTTL.SESSION_MEDIUM,
    ) -> bool:
        key = cls._get_session_key(session_id)
        return CacheService.set(key, meta, ttl)

    @classmethod
    def get_session_history(cls, session_id: str) -> Optional[List[Dict]]:
        key = cls._get_history_key(session_id)
        return CacheService.get(key)

    @classmethod
    def cache_session_history(
        cls,
        session_id: str,
        history: List[Dict],
        ttl: int = CacheTTL.SESSION_MEDIUM,
    ) -> bool:
        key = cls._get_history_key(session_id)
        return CacheService.set(key, history, ttl)

    @classmethod
    def invalidate_session(cls, session_id: str) -> bool:
        CacheService.delete(cls._get_session_key(session_id))
        CacheService.delete(cls._get_history_key(session_id))
        logger.info(f"会话缓存已失效: {session_id}")
        return True

    @classmethod
    def invalidate_all_sessions(cls) -> int:
        CacheService.delete_pattern(f"{CACHE_PREFIX_SESSION}:*")
        logger.info("所有会话缓存已失效")
        return 1


# ==================== Agent 状态缓存服务 ====================

class AgentStateCacheService:
    """
    Agent 状态缓存服务

    缓存 Agent 运行时状态（工具列表、中间结果等），
    用于断点续跑和状态恢复
    """

    @classmethod
    def _get_agent_key(cls, thread_id: str) -> str:
        return f"{CACHE_PREFIX_AGENT}:{thread_id}"

    @classmethod
    def get_agent_state(cls, thread_id: str) -> Optional[Dict]:
        key = cls._get_agent_key(thread_id)
        return CacheService.get(key)

    @classmethod
    def cache_agent_state(
        cls,
        thread_id: str,
        state: Dict,
        ttl: int = CacheTTL.AGENT_STATE,
    ) -> bool:
        key = cls._get_agent_key(thread_id)
        return CacheService.set(key, state, ttl)

    @classmethod
    def invalidate_agent_state(cls, thread_id: str) -> bool:
        key = cls._get_agent_key(thread_id)
        return CacheService.delete(key)


# ==================== Redis 原生操作 ====================

class RedisDirectClient:
    """
    Redis 原生客户端

    提供 Django cache 框架不直接支持的操作：
    - SCAN 按模式扫描 key
    - TTL 查询
    - INFO 服务器信息
    """

    _client = None

    @classmethod
    def get_client(cls):
        if cls._client is None:
            try:
                import redis
                redis_url = getattr(django_settings, 'CACHES', {}).get(
                    'default', {}
                ).get('LOCATION', 'redis://127.0.0.1:6379/1')
                cls._client = redis.from_url(redis_url, decode_responses=True)
            except Exception as e:
                logger.error(f"Redis 客户端初始化失败: {e}")
                return None
        return cls._client

    @classmethod
    def scan_keys(cls, pattern: str, count: int = 100) -> List[str]:
        client = cls.get_client()
        if not client:
            return []
        try:
            keys = []
            cursor = 0
            while True:
                cursor, batch = client.scan(
                    cursor=cursor, match=pattern, count=count
                )
                keys.extend(batch)
                if cursor == 0:
                    break
            return keys
        except Exception as e:
            logger.error(f"SCAN 失败: {e}")
            return []

    @classmethod
    def get_ttl(cls, key: str) -> int:
        client = cls.get_client()
        if not client:
            return -2
        try:
            return client.ttl(key)
        except Exception as e:
            logger.error(f"TTL 查询失败: {e}")
            return -2

    @classmethod
    def get_info(cls) -> Optional[Dict]:
        client = cls.get_client()
        if not client:
            return None
        try:
            info = client.info()
            return {
                'used_memory_human': info.get('used_memory_human'),
                'connected_clients': info.get('connected_clients'),
                'uptime_in_seconds': info.get('uptime_in_seconds'),
                'total_commands_processed': info.get('total_commands_processed'),
                'keyspace_hits': info.get('keyspace_hits'),
                'keyspace_misses': info.get('keyspace_misses'),
                'db_size': client.dbsize(),
            }
        except Exception as e:
            logger.error(f"Redis INFO 失败: {e}")
            return None

    @classmethod
    def delete_keys_by_pattern(cls, pattern: str) -> int:
        keys = cls.scan_keys(pattern)
        if not keys:
            return 0
        client = cls.get_client()
        if not client:
            return 0
        try:
            deleted = client.delete(*keys)
            logger.info(f"按模式删除 {deleted} 个 key: {pattern}")
            return deleted
        except Exception as e:
            logger.error(f"批量删除失败: {e}")
            return 0


# ==================== 缓存失效策略 ====================

class CacheInvalidationStrategy:
    """
    缓存失效策略

    在数据变更时自动使相关缓存失效，保证数据一致性
    """

    @classmethod
    def on_index_created(cls, index_name: str):
        CacheService.delete(f"{CACHE_PREFIX_INDEX}:{index_name}")
        logger.info(f"缓存失效（索引创建）: {index_name}")

    @classmethod
    def on_index_updated(cls, index_name: str):
        QueryCacheService.invalidate_index_queries(index_name)
        CacheService.delete(f"{CACHE_PREFIX_INDEX}:{index_name}")
        CacheService.delete_pattern(f"{CACHE_PREFIX_VECTOR}:{index_name}:*")
        logger.info(f"缓存失效（索引更新）: {index_name}")

    @classmethod
    def on_index_deleted(cls, index_name: str):
        QueryCacheService.invalidate_index_queries(index_name)
        CacheService.delete(f"{CACHE_PREFIX_INDEX}:{index_name}")
        CacheService.delete_pattern(f"{CACHE_PREFIX_VECTOR}:{index_name}:*")
        logger.info(f"缓存失效（索引删除）: {index_name}")

    @classmethod
    def on_document_added(cls, index_name: str):
        QueryCacheService.invalidate_index_queries(index_name)
        CacheService.delete(f"{CACHE_PREFIX_INDEX}:{index_name}")
        logger.info(f"缓存失效（文档添加）: {index_name}")

    @classmethod
    def on_document_deleted(cls, index_name: str):
        QueryCacheService.invalidate_index_queries(index_name)
        CacheService.delete(f"{CACHE_PREFIX_INDEX}:{index_name}")
        logger.info(f"缓存失效（文档删除）: {index_name}")

    @classmethod
    def on_session_updated(cls, session_id: str):
        SessionCacheService.invalidate_session(session_id)
        logger.info(f"缓存失效（会话更新）: {session_id}")

    @classmethod
    def on_session_deleted(cls, session_id: str):
        SessionCacheService.invalidate_session(session_id)
        logger.info(f"缓存失效（会话删除）: {session_id}")

    @classmethod
    def on_research_completed(cls, thread_id: str):
        AgentStateCacheService.invalidate_agent_state(thread_id)
        logger.info(f"缓存失效（研究完成）: {thread_id}")

    @classmethod
    def invalidate_all(cls) -> Dict[str, int]:
        results = {}
        for prefix_name, prefix in [
            ('query', CACHE_PREFIX_QUERY),
            ('model', CACHE_PREFIX_MODEL),
            ('vector', CACHE_PREFIX_VECTOR),
            ('tool', CACHE_PREFIX_TOOL),
            ('embedding', CACHE_PREFIX_EMBEDDING),
            ('index', CACHE_PREFIX_INDEX),
            ('session', CACHE_PREFIX_SESSION),
            ('agent', CACHE_PREFIX_AGENT),
        ]:
            count = RedisDirectClient.delete_keys_by_pattern(f"{prefix}:*")
            results[prefix_name] = count
        logger.info(f"全量缓存失效: {results}")
        return results
