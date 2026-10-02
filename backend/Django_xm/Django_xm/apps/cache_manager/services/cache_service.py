"""
Redis 缓存服务 - 通用缓存增强模块

提供以下缓存策略：
1. 热点查询缓存：缓存 RAG 检索结果、常用问答
2. 模型响应缓存：缓存相同输入的 AI 回复
3. 向量检索缓存：缓存 embedding 和相似度搜索结果
4. 会话缓存：由独立的 SecureSessionCacheService 承担（见 secure_session_cache.py）
5. 缓存运维：失效策略、Redis 直连统计、LangChain LLM Cache

设计原则：
- 使用前缀命名空间避免 key 冲突
- 支持可配置的 TTL
- 支持集中式缓存失效策略
- 统计缓存命中率
"""

import asyncio
import hashlib
import logging
import threading
from collections.abc import Sequence
from typing import Any

from django.core.cache import cache
from django.utils import timezone
from langchain_core.caches import BaseCache
from langchain_core.outputs import Generation

logger = logging.getLogger(__name__)


# ==================== 缓存前缀常量 ====================

CACHE_PREFIX_QUERY = "rag_query"
CACHE_PREFIX_MODEL = "model_response"
CACHE_PREFIX_EMBEDDING = "embedding"
CACHE_PREFIX_VECTOR = "vector_search"

# 跨 app 散落前缀（各自有独立写入方，统一登记以便全量清理与分类统计；
# 键值与历史字面量严格一致，故不会造成任何缓存失配）
CACHE_PREFIX_DASHBOARD = "analytics:dashboard"
CACHE_PREFIX_USAGE_STATS = "users:usage_stats"
CACHE_PREFIX_CHAT = "chat"  # chat:commands / chat:modes
CACHE_PREFIX_PROJECT_CONTEXT = "project_context"
CACHE_PREFIX_SUGGESTIONS = "suggestions"
# 历史注记：曾存在 user_mcp_servers（tools/mcp/discovery）——把 MCP 配置当缓存存
# （ttl=None、含 auth_token），与其 DB 体系（McpServerConfig）重复且零调用方，
# 已于 2026-10-01 随死路径删除。教训：配置数据绝不存缓存层。

CACHE_PREFIX_LABELS = {
    CACHE_PREFIX_QUERY: "RAG 查询缓存",
    CACHE_PREFIX_MODEL: "模型响应缓存",
    CACHE_PREFIX_EMBEDDING: "Embedding 缓存",
    CACHE_PREFIX_VECTOR: "向量检索缓存",
    CACHE_PREFIX_DASHBOARD: "分析看板缓存",
    CACHE_PREFIX_USAGE_STATS: "用量统计缓存",
    CACHE_PREFIX_CHAT: "聊天配置缓存",
    CACHE_PREFIX_PROJECT_CONTEXT: "项目上下文缓存",
    CACHE_PREFIX_SUGGESTIONS: "建议缓存",
}


def get_redis_client():
    """获取 Redis 客户端（统一入口）

    优先通过 Django cache 框架获取，回退到 RedisDirectClient。
    """
    try:
        from django.core.cache import caches

        default_cache = caches["default"]
        if hasattr(default_cache, "client"):
            client = default_cache.client
            if hasattr(client, "get_client"):
                return client.get_client()
            return client
    except Exception as e:
        logger.warning(f"通过 Django cache 获取 Redis 客户端失败: {e}")
    return RedisDirectClient.get_client()


def get_redis_info():
    """获取 Redis info 字典"""
    client = get_redis_client()
    if client is None:
        return None
    try:
        return client.info()
    except Exception:
        logger.exception("获取 Redis info 失败")
        return None


# ==================== 缓存 TTL 配置 ====================


class CacheTTL:
    """缓存过期时间配置（秒）"""

    QUERY_SHORT = 300
    QUERY_MEDIUM = 1800
    QUERY_LONG = 86400
    MODEL_MEDIUM = 3600
    EMBEDDING = 604800
    CONFIG_LONG = 86400  # chat 配置类缓存（命令列表/模式列表），历史上误名 TOOL_LONG
    VECTOR_SEARCH = 3600


# ==================== 通用缓存工具类 ====================


class CacheService:
    """
    通用 Redis 缓存服务

    提供统一的缓存操作接口，支持：
    - 类型安全的存取
    - 自动序列化/反序列化
    - 缓存统计（线程安全）
    """

    _hit_count = 0
    _miss_count = 0
    _stats_lock = threading.Lock()
    # get 的未命中哨兵：与任何真实缓存值/调用方 default 严格区分，
    # 保证命中统计不受缓存值恰为 False/0/[]/None 等值的影响
    _SENTINEL = object()

    @classmethod
    def _increment_hit(cls):
        with cls._stats_lock:
            cls._hit_count += 1

    @classmethod
    def _increment_miss(cls):
        with cls._stats_lock:
            cls._miss_count += 1

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
            value = cache.get(key, cls._SENTINEL)
            if value is not cls._SENTINEL:
                cls._increment_hit()
                return value
            cls._increment_miss()
            return default
        except Exception:
            logger.exception(f"缓存读取失败: {key}, 错误")
            cls._increment_miss()
            return default

    @classmethod
    def set(cls, key: str, value: Any, ttl: int | None = CacheTTL.QUERY_MEDIUM) -> bool:
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
        except Exception:
            logger.exception(f"缓存设置失败: {key}, 错误")
            return False

    @classmethod
    def delete(cls, key: str) -> bool:
        """删除缓存"""
        try:
            cache.delete(key)
            logger.debug(f"缓存删除: {key}")
            return True
        except Exception:
            logger.exception(f"缓存删除失败: {key}, 错误")
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

            default_cache = caches["default"]

            if hasattr(default_cache, "delete_pattern"):
                # django-redis 的 delete_pattern 返回真实删除的 key 数
                count = default_cache.delete_pattern(pattern) or 0
                logger.info(f"缓存模式删除: {pattern}, 删除 {count} 个 key")
                return count
            return 0
        except Exception:
            logger.exception(f"缓存模式删除失败: {pattern}, 错误")
            return 0

    @classmethod
    def get_stats(cls) -> dict[str, Any]:
        with cls._stats_lock:
            total = cls._hit_count + cls._miss_count
            hit_rate = (cls._hit_count / total * 100) if total > 0 else 0
            return {
                "hit_count": cls._hit_count,
                "miss_count": cls._miss_count,
                "total_requests": total,
                "hit_rate": round(hit_rate, 2),
            }

    @classmethod
    def reset_stats(cls):
        with cls._stats_lock:
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
    # 哈希策略统一为 SHA256 前 24 位（96 bit），与 RedisLangChainCache._make_key 一致：
    # MD5[:12] 仅 48 bit，生日碰撞临界点约 1600 万条目，碰撞会静默返回错误缓存值
    query_hash = hashlib.sha256(query.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    return f"{CACHE_PREFIX_QUERY}:{index_name}:{query_hash}:k{k}"


def generate_model_cache_key(prompt: str, model_name: str, mode: str = "default", tools_hash: str | None = None) -> str:
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
    prompt_hash = hashlib.sha256(prompt.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    tools_part = f":t{tools_hash}" if tools_hash else ""
    return f"{CACHE_PREFIX_MODEL}:{model_name}:{mode}:{prompt_hash}{tools_part}"


def generate_embedding_cache_key(text: str, model: str = "default") -> str:
    """生成 embedding 缓存键"""
    text_hash = hashlib.sha256(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    return f"{CACHE_PREFIX_EMBEDDING}:{model}:{text_hash}"


def generate_vector_search_cache_key(
    query: str, index_name: str, k: int = 4, score_threshold: float | None = None
) -> str:
    """生成向量搜索缓存键"""
    query_hash = hashlib.sha256(query.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    threshold_part = f":th{score_threshold}" if score_threshold else ""
    return f"{CACHE_PREFIX_VECTOR}:{index_name}:{query_hash}:k{k}{threshold_part}"


# ==================== 专用缓存服务 ====================


class QueryCacheService:
    """
    热点查询缓存服务

    缓存 RAG 检索结果和常用问答，避免重复检索和生成
    """

    @classmethod
    def get_cached_query(cls, query: str, index_name: str, k: int = 4) -> dict | None:
        """获取缓存的查询结果"""
        key = generate_query_cache_key(query, index_name, k)
        return CacheService.get(key)

    @classmethod
    def cache_query_result(
        cls, query: str, result: dict, index_name: str, k: int = 4, ttl: int = CacheTTL.QUERY_MEDIUM
    ) -> bool:
        """缓存查询结果"""
        key = generate_query_cache_key(query, index_name, k)

        cache_data = {
            "query": query,
            "result": result,
            "cached_at": timezone.now().isoformat(),
            "index": index_name,
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
    def get_cached_response(cls, prompt: str, model_name: str, mode: str = "default") -> dict | None:
        """获取缓存的模型响应"""
        key = generate_model_cache_key(prompt, model_name, mode)
        return CacheService.get(key)

    @classmethod
    def cache_model_response(
        cls, prompt: str, response: dict, model_name: str, mode: str = "default", ttl: int = CacheTTL.MODEL_MEDIUM
    ) -> bool:
        """缓存模型响应"""
        key = generate_model_cache_key(prompt, model_name, mode)

        cache_data = {
            "prompt": prompt,
            "response": response,
            "model": model_name,
            "mode": mode,
            "cached_at": timezone.now().isoformat(),
        }

        return CacheService.set(key, cache_data, ttl)

    @classmethod
    def invalidate_model_cache(cls, model_name: str) -> bool:
        """使某个模型的所有缓存失效"""
        pattern = f"{CACHE_PREFIX_MODEL}:{model_name}:*"
        CacheService.delete_pattern(pattern)
        logger.info(f"模型缓存已失效: {model_name}")
        return True


class VectorSearchCacheService:
    """
    向量搜索缓存服务

    缓存 embedding 计算和相似度搜索结果
    """

    @classmethod
    def get_cached_search(cls, query: str, index_name: str, k: int = 4) -> list | None:
        """获取缓存的向量搜索结果"""
        key = generate_vector_search_cache_key(query, index_name, k)
        return CacheService.get(key)

    @classmethod
    def cache_search_result(
        cls, query: str, results: list, index_name: str, k: int = 4, ttl: int = CacheTTL.VECTOR_SEARCH
    ) -> bool:
        """缓存向量搜索结果"""
        key = generate_vector_search_cache_key(query, index_name, k)
        return CacheService.set(key, results, ttl)

    @classmethod
    def get_cached_embedding(cls, text: str, model: str = "default") -> list[float] | None:
        """获取缓存的 embedding"""
        key = generate_embedding_cache_key(text, model)
        return CacheService.get(key)

    @classmethod
    def cache_embedding(
        cls, text: str, embedding: list[float], model: str = "default", ttl: int = CacheTTL.EMBEDDING
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


# ==================== 缓存健康检查 ====================


class CacheHealthChecker:
    """缓存健康检查"""

    @classmethod
    def check_connection(cls) -> bool:
        try:
            cache.set("health_check", "ok", timeout=10)
            result = cache.get("health_check")
            return result == "ok"
        except Exception:
            logger.exception("Redis 连接检查失败")
            return False

    @classmethod
    def get_health_info(cls) -> dict[str, Any]:
        connection_ok = cls.check_connection()
        stats = CacheService.get_stats()

        return {
            "connection": "healthy" if connection_ok else "unhealthy",
            "stats": stats,
            "checked_at": timezone.now().isoformat(),
        }


# ==================== Redis 原生操作 ====================


class RedisDirectClient:
    """
    Redis 原生客户端

    提供 Django cache 框架不直接支持的操作：
    - SCAN 按模式扫描 key
    - INFO 服务器信息
    """

    _client = None

    @classmethod
    def _get_redis_config(cls):
        import os

        from Django_xm.apps.core.config import settings as project_cfg

        redis_url = os.environ.get("REDIS_URL", project_cfg.redis_url)
        redis_password = os.environ.get("REDIS_PASSWORD", project_cfg.redis_password) or None
        return redis_url, redis_password

    @classmethod
    def get_client(cls):
        if cls._client is not None:
            try:
                cls._client.ping()
                return cls._client
            except Exception:
                cls._client = None

        try:
            import redis

            redis_url, redis_password = cls._get_redis_config()
            cls._client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                password=redis_password,
            )
            cls._client.ping()
        except Exception:
            logger.exception("Redis 客户端初始化失败")
            cls._client = None
        return cls._client

    @classmethod
    def scan_keys(cls, pattern: str, count: int = 100) -> list[str]:
        client = cls.get_client()
        if not client:
            return []
        try:
            keys = []
            cursor = 0
            while True:
                cursor, batch = client.scan(cursor=cursor, match=pattern, count=count)
                keys.extend(batch)
                if cursor == 0:
                    break
            return keys
        except Exception:
            logger.exception("SCAN 失败")
            return []

    @classmethod
    def get_info(cls) -> dict | None:
        client = cls.get_client()
        if not client:
            return None
        try:
            info = client.info()
            return {
                "used_memory_human": info.get("used_memory_human"),
                "connected_clients": info.get("connected_clients"),
                "uptime_in_seconds": info.get("uptime_in_seconds"),
                "total_commands_processed": info.get("total_commands_processed"),
                "keyspace_hits": info.get("keyspace_hits"),
                "keyspace_misses": info.get("keyspace_misses"),
                "db_size": client.dbsize(),
            }
        except Exception:
            logger.exception("Redis INFO 失败")
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
        except Exception:
            logger.exception("批量删除失败")
            return 0


# ==================== LangChain LLM Cache 层 ====================


class RedisLangChainCache(BaseCache):
    """
    LangChain LLM Cache 的 Redis 实现

    正式继承 langchain_core.caches.BaseCache（官方抽象基类），
    将 LLM 的 prompt+llm_string -> Generation 序列缓存到 Redis，
    避免对相同 prompt+配置重复调用 API。

    key 生成对齐官方语义（BaseCache.lookup docstring 要求）：
    由 prompt 与 llm_string（LLM 调用参数的序列化串，含模型名/温度/
    max_tokens 等）二元组联合哈希，保证不同模型/配置互不串缓存。

    使用方式:
        from langchain_core.globals import set_llm_cache
        set_llm_cache(RedisLangChainCache())

    参考:
        https://python.langchain.com/docs/how_to/llm_caching/
    """

    CACHE_PREFIX = "langchain:llm_cache"

    def __init__(self, ttl: int = CacheTTL.MODEL_MEDIUM):
        self._ttl = ttl

    def _make_key(self, prompt: str, llm_string: str) -> str:
        # 官方要求 key 由 (prompt, llm_string) 二元组生成；
        # 加 "\x00" 分隔符消除两段拼接的边界歧义（官方 _hash 为裸拼接）
        combined = hashlib.sha256(f"{prompt}\x00{llm_string}".encode("utf-8"))
        return f"{self.CACHE_PREFIX}:{combined.hexdigest()[:24]}"

    def lookup(self, prompt: str, llm_string: str) -> list[Generation] | None:
        key = self._make_key(prompt, llm_string)
        cached = CacheService.get(key)
        if cached is not None:
            logger.debug(f"LangChain Cache 命中: {key}")
            return cached
        return None

    def update(self, prompt: str, llm_string: str, return_val: Sequence[Generation]) -> None:
        key = self._make_key(prompt, llm_string)
        CacheService.set(key, list(return_val), self._ttl)
        logger.debug(f"LangChain Cache 更新: {key}")

    def clear(self, **kwargs: Any) -> None:
        CacheService.delete_pattern(f"{self.CACHE_PREFIX}:*")
        logger.info("LangChain Cache 已清空")

    async def alookup(self, prompt: str, llm_string: str) -> list[Generation] | None:
        key = self._make_key(prompt, llm_string)
        cached = await asyncio.to_thread(CacheService.get, key)
        if cached is not None:
            logger.debug(f"LangChain Cache 异步命中: {key}")
            return cached
        return None

    async def aupdate(self, prompt: str, llm_string: str, return_val: Sequence[Generation]) -> None:
        key = self._make_key(prompt, llm_string)
        await asyncio.to_thread(CacheService.set, key, list(return_val), self._ttl)
        logger.debug(f"LangChain Cache 异步更新: {key}")

    async def aclear(self, **kwargs: Any) -> None:
        await asyncio.to_thread(CacheService.delete_pattern, f"{self.CACHE_PREFIX}:*")
        logger.info("LangChain Cache 异步清空")


def setup_langchain_cache(enabled: bool = True) -> None:
    """
    全局设置 LangChain LLM Cache

    在 Django App ready() 时调用，将 Redis 缓存注册为
    LangChain 全局 LLM Cache，使所有 LLM 调用自动缓存。

    Args:
        enabled: 是否启用缓存
    """
    if not enabled:
        logger.info("LangChain LLM Cache 未启用")
        return

    try:
        from langchain_core.globals import set_llm_cache

        cache_instance = RedisLangChainCache()
        set_llm_cache(cache_instance)
        logger.info("LangChain LLM Cache 已启用（Redis 后端）")
    except ImportError:
        logger.warning("langchain_core.globals.set_llm_cache 不可用，请升级 langchain-core")
    except Exception as e:
        logger.warning(f"LangChain Cache 设置失败: {e}")


# ==================== 业务级统一缓存失效接口 ====================


def invalidate_chat_cache(user_id=None):
    """统一聊天缓存失效接口

    在会话创建/更新/删除等操作后调用，
    使 analytics 的 dashboard 统计缓存失效。

    Args:
        user_id: 用户 ID
    """
    if user_id is not None:
        # 键名必须与唯一写入方对齐：analytics_service 写入
        # "analytics:dashboard:user_{id}"（修复前误删 "dashboard:user_{id}"，
        # 与真实键错配导致失效永不命中，仅靠 300s 短 TTL 兜底）
        CacheService.delete(f"analytics:dashboard:user_{user_id}")


def invalidate_knowledge_cache(user_id=None, user_index_name=None):
    """统一知识库缓存失效接口

    在知识库/文档增删改操作后调用，
    使查询缓存、向量搜索缓存失效。

    Args:
        user_id: 用户 ID（保留参数兼容既有调用方；历史上失效的
            kb_list:/doc_list: 键全仓无写入方，已随死键清理移除）
        user_index_name: 用户索引名（如 user_1_my_kb），失效查询、向量搜索缓存
    """
    if user_index_name is not None:
        QueryCacheService.invalidate_index_queries(user_index_name)
        VectorSearchCacheService.invalidate_index_queries(user_index_name)
