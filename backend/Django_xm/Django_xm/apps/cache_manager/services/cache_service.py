"""
Redis 缓存服务 - 通用缓存增强模块

提供以下缓存策略：
1. 热点查询缓存：缓存 RAG 检索结果、常用问答
2. 向量检索缓存：缓存 embedding 和相似度搜索结果
3. 会话缓存：由独立的 SecureSessionCacheService 承担（见 secure_session_cache.py）
4. 缓存运维：失效策略、Redis 直连统计

设计原则：
- 使用前缀命名空间避免 key 冲突
- 支持可配置的 TTL
- 支持集中式缓存失效策略
- 统计缓存命中率
"""

import hashlib
import logging
import threading
from typing import Any

from django.core.cache import cache
from django.utils import timezone

# 5-2 收敛：同步 Redis 客户端获取唯一出口在 common/redis_utils（本模块仅再导出，
# 供 cache_manager.views 等既有消费方沿用导入路径；RedisDirectClient 同步委托）
from Django_xm.common.redis_utils import get_direct_redis_client, get_redis_client

logger = logging.getLogger(__name__)


# ==================== 缓存前缀常量 ====================

CACHE_PREFIX_QUERY = "rag_query"
CACHE_PREFIX_EMBEDDING = "embedding"
CACHE_PREFIX_VECTOR = "vector_search"
# Agent/学习/附件检索结果缓存（rag_agent_cache_ttl 驱动；只缓存非空文档列表）
CACHE_PREFIX_AGENT_DOCS = "rag_agent_docs"

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
# 历史注记：模型响应缓存已整体移除（2026-10-18）。曾先后存在两条轨道——
# langchain 框架级 LLM 缓存（2026-10-03 下线，审计 5-1.5）与业务级
# ModelResponseCacheService（键为整条 prompt 的 SHA256，含用户命名空间）；
# 后者经静态排查与运行时探针双重确认在生产代码零写入方（仅 tests 覆盖），
# 彻底删除而非接入，原因：agent 的 prompt 每轮都被 MemoryInjectionMiddleware /
# 上下文压缩 / AttentionGuide 重排动态改写，整条 prompt SHA 键的命中率结构性
# 趋近 0，且重放响应会绕过 LazyFallbackChatModel 的熔断与降级判定、有重放
# 过期上下文的风险。若未来要缓存模型调用，先在「无状态、可重复」的调用点
# （如压缩/摘要模型）量化命中率，再决定是否引入。
# 教训：同一能力域只保留一条缓存轨道，失效才有单一权威——但"单轨"必须先有写入方。

CACHE_PREFIX_LABELS = {
    CACHE_PREFIX_QUERY: "RAG 查询缓存",
    CACHE_PREFIX_EMBEDDING: "Embedding 缓存",
    CACHE_PREFIX_VECTOR: "向量检索缓存",
    CACHE_PREFIX_AGENT_DOCS: "Agent 检索结果缓存",
    CACHE_PREFIX_DASHBOARD: "分析看板缓存",
    CACHE_PREFIX_USAGE_STATS: "用量统计缓存",
    CACHE_PREFIX_CHAT: "聊天配置缓存",
    CACHE_PREFIX_PROJECT_CONTEXT: "项目上下文缓存",
    CACHE_PREFIX_SUGGESTIONS: "建议缓存",
}


# （5-2 收敛：原本地 get_redis_client（Django cache 优先 + RedisDirectClient 兜底）
#  实现已收敛至 common/redis_utils.get_redis_client，经顶部导入再导出）


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
    def get_many(cls, keys: list[str]) -> dict[str, Any]:
        """批量读取缓存（单次 Redis 往返，RP-11）

        Args:
            keys: 缓存键列表

        Returns:
            {key: value}，仅包含命中的键；后端故障时返回 {}（降级为未命中，
            不阻塞调用方——与逐条 get 的故障语义一致）
        """
        if not keys:
            return {}
        try:
            values = cache.get_many(keys)
        except Exception:
            logger.exception(f"缓存批量读取失败: {len(keys)} 个键")
            return {}
        with cls._stats_lock:
            cls._hit_count += len(values)
            cls._miss_count += len(keys) - len(values)
        return values

    @classmethod
    def set_many(cls, data: dict[str, Any], ttl: int | None = CacheTTL.QUERY_MEDIUM) -> int:
        """批量写入缓存（单次 Redis 往返，RP-11）

        Returns:
            提交写入的键数（后端批量接口不返回逐键失败明细；异常时整体计 0）
        """
        if not data:
            return 0
        try:
            cache.set_many(data, timeout=ttl)
            logger.debug(f"缓存批量设置: {len(data)} 个键, TTL={ttl}s")
            return len(data)
        except Exception:
            logger.exception(f"缓存批量设置失败: {len(data)} 个键")
            return 0

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


def generate_query_cache_key(
    query: str,
    index_name: str,
    k: int = 4,
    model_name: str = "default",
    rag_mode: str = "default",
) -> str:
    """
    生成 RAG 查询缓存键

    键结构（B5 起）::

        rag_query:{index_name}:{query_hash}:k{k}:m{model_name}:r{rag_mode}

    模型/模式段（B5）：同一查询在不同 chat 模型或 RAG 模式下答案可能不同，
    旧键缺这两段时换模型/换模式会命中过期答案缓存。取不到真实值时调用方传
    "default" 占位（键结构变化后旧键自然失配，随 TTL 过期，无需迁移）。
    按索引失效（invalidate_index_queries 的 ``rag_query:{index}:*`` 通配）
    不受段追加影响。

    Args:
        query: 查询文本
        index_name: 索引名称
        k: 检索文档数
        model_name: 当前 chat 模型标识（如 "openai:gpt-4o"），取不到传 "default"
        rag_mode: RAG 模式标识（如 "strict"），取不到传 "default"

    Returns:
        缓存键
    """
    # 哈希策略统一为 SHA256 前 24 位（96 bit）：
    # MD5[:12] 仅 48 bit，生日碰撞临界点约 1600 万条目，碰撞会静默返回错误缓存值
    query_hash = hashlib.sha256(query.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    return f"{CACHE_PREFIX_QUERY}:{index_name}:{query_hash}:k{k}:m{model_name}:r{rag_mode}"


def generate_embedding_cache_key(text: str, model: str = "default") -> str:
    """生成 embedding 缓存键"""
    text_hash = hashlib.sha256(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    return f"{CACHE_PREFIX_EMBEDDING}:{model}:{text_hash}"


def generate_vector_search_cache_key(
    query: str, index_name: str, k: int = 4, score_threshold: float | None = None
) -> str:
    """生成向量搜索缓存键"""
    query_hash = hashlib.sha256(query.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    # B15：显式 is not None——threshold=0.0 为 falsy 但与"未设阈值"语义不同，
    # 须区分缓存键，避免 0.0 阈值请求命中无阈值结果
    threshold_part = f":th{score_threshold}" if score_threshold is not None else ""
    return f"{CACHE_PREFIX_VECTOR}:{index_name}:{query_hash}:k{k}{threshold_part}"


# ==================== 专用缓存服务 ====================


class QueryCacheService:
    """
    热点查询缓存服务

    缓存 RAG 检索结果和常用问答，避免重复检索和生成
    """

    @classmethod
    def get_cached_query(
        cls, query: str, index_name: str, k: int = 4, model_name: str = "default", rag_mode: str = "default"
    ) -> dict | None:
        """获取缓存的查询结果（model_name/rag_mode 须与写入时一致，否则未命中）"""
        key = generate_query_cache_key(query, index_name, k, model_name=model_name, rag_mode=rag_mode)
        return CacheService.get(key)

    @classmethod
    def cache_query_result(
        cls,
        query: str,
        result: dict,
        index_name: str,
        k: int = 4,
        ttl: int = CacheTTL.QUERY_MEDIUM,
        model_name: str = "default",
        rag_mode: str = "default",
    ) -> bool:
        """缓存查询结果"""
        key = generate_query_cache_key(query, index_name, k, model_name=model_name, rag_mode=rag_mode)

        cache_data = {
            "query": query,
            "result": result,
            "cached_at": timezone.now().isoformat(),
            "index": index_name,
        }

        return CacheService.set(key, cache_data, ttl)

    @classmethod
    def invalidate_index_queries(cls, index_name: str) -> int:
        """清除指定索引的所有查询缓存"""
        pattern = f"{CACHE_PREFIX_QUERY}:{index_name}:*"
        count = CacheService.delete_pattern(pattern)
        logger.info(f"索引查询缓存已失效: {index_name}, 清除 {count} 个键")
        return count


class VectorSearchCacheService:
    """
    向量搜索缓存服务

    缓存 embedding 计算和相似度搜索结果
    """

    @classmethod
    def get_cached_search(
        cls, query: str, index_name: str, k: int = 4, score_threshold: float | None = None
    ) -> list | None:
        """获取缓存的向量搜索结果（score_threshold 参与键，避免阈值变更后命中旧结果）"""
        key = generate_vector_search_cache_key(query, index_name, k, score_threshold)
        return CacheService.get(key)

    @classmethod
    def cache_search_result(
        cls,
        query: str,
        results: list,
        index_name: str,
        k: int = 4,
        ttl: int = CacheTTL.VECTOR_SEARCH,
        score_threshold: float | None = None,
    ) -> bool:
        """缓存向量搜索结果（score_threshold 参与键，读写同口径）"""
        key = generate_vector_search_cache_key(query, index_name, k, score_threshold)
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
    def get_cached_embeddings_batch(cls, texts: list[str], model: str = "default") -> dict[str, list[float]]:
        """批量获取缓存 embedding（RP-11：单次 Redis 往返替代逐条 GET）

        Returns:
            {text: vector}，仅包含命中的文本
        """
        if not texts:
            return {}
        key_to_text = {generate_embedding_cache_key(text, model): text for text in texts}
        found = CacheService.get_many(list(key_to_text))
        return {key_to_text[key]: vector for key, vector in found.items()}

    @classmethod
    def cache_embeddings_batch(
        cls, items: list[tuple[str, list[float]]], model: str = "default", ttl: int = CacheTTL.EMBEDDING
    ) -> int:
        """批量缓存 embedding（RP-11：单次 Redis 往返替代逐条 SET）"""
        data = {generate_embedding_cache_key(text, model): vector for text, vector in items}
        return CacheService.set_many(data, ttl)

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
    Redis 原生操作门面（5-2 收敛：客户端获取实现委托 common/redis_utils）

    提供 Django cache 框架不直接支持的操作：
    - SCAN 按模式扫描 key
    - INFO 服务器信息

    客户端本身经 :func:`RedisDirectClient.get_client` 委托
    ``common.redis_utils.get_direct_redis_client`` 获取（decode_responses=True、
    socket 超时 3s、进程级单例 + ping 重连；连接参数单一来源
    ``common/connections.get_redis_config()``）。
    """

    @classmethod
    def get_client(cls):
        """获取直连客户端（委托 common/redis_utils 唯一出口）。"""
        return get_direct_redis_client()

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


# ==================== LangChain LLM Cache 层 ====================
# 历史注记：本节曾承载 langchain 框架级 LLM 缓存（BaseCache 实现，经 langchain
# 全局缓存 API 接线）——与业务级模型响应缓存功能重叠、双轨失效不联动
# （审计 5-1.5），已于 2026-10-03 整体下线；模型响应缓存轨道已于 2026-10-18
# 整体移除（见文件头历史注记）。


# ==================== 业务级统一缓存失效接口 ====================


def invalidate_chat_cache(user_id=None):
    """统一聊天缓存失效接口

    在会话创建/更新/删除等操作后调用，
    使 analytics 的 dashboard 统计缓存失效。

    Args:
        user_id: 用户 ID
    """
    if user_id is not None:
        # 键名必须与唯一写入方对齐：analytics_service 用 CACHE_PREFIX_DASHBOARD 写入
        # "analytics:dashboard:user_{id}"。此处**必须复用同一常量**，不可裸写字符串：
        # 历史 bug 正是误删 "dashboard:user_{id}"（漏了 analytics: 前缀），与真实键
        # 错配导致失效永不命中，只能靠 300s 短 TTL 兜底。前缀一旦变更，两处必须同步。
        CacheService.delete(f"{CACHE_PREFIX_DASHBOARD}:user_{user_id}")


def invalidate_knowledge_cache(user_id=None, user_index_name=None):
    """统一知识库缓存失效接口

    在知识库/文档增删改操作后调用，
    使查询缓存、向量搜索缓存、Agent 检索结果缓存失效。

    Args:
        user_id: 用户 ID（保留参数兼容既有调用方；历史上失效的
            kb_list:/doc_list: 键全仓无写入方，已随死键清理移除）
        user_index_name: 用户索引名（如 user_1_my_kb），失效查询、向量搜索缓存
    """
    if user_index_name is not None:
        QueryCacheService.invalidate_index_queries(user_index_name)
        VectorSearchCacheService.invalidate_index_queries(user_index_name)
        # Agent/学习/附件的检索结果缓存（rag_agent_cache_ttl）：索引内容变了必须失效，
        # 否则 TTL 窗口内 Agent 仍拿到旧文档。前缀常量与写入侧共用，不裸写字符串。
        CacheService.delete_pattern(f"{CACHE_PREFIX_AGENT_DOCS}:{user_index_name}:*")
