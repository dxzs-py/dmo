"""LLM 缓存与速率限制基础设施

从 llm_factory.py 拆分而来，职责：
1. 全局 LLM 缓存（InMemoryCache / RedisSemanticCache）管理
2. 速率限制器（InMemoryRateLimiter）单例
3. 模型创建的统一缓存封装（_cached_model_creation）

依赖关系：
- llm_cache 是底层基础设施，不依赖 llm_factory / llm_fallback
- 上层 llm_factory 通过 _cached_model_creation 共用缓存逻辑

参考：
- https://docs.langchain.com/oss/python/langchain/caching
- https://reference.langchain.com/python/langchain/rate_limiters
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from Django_xm.apps.core.config import get_logger

from ..config import settings
from .model_cache import get_cached_model, set_cached_model

logger = get_logger(__name__)


# ============== 全局单例锁与状态 ==============

_rate_limiter_lock = threading.Lock()
_rate_limiter: Any | None = None
_llm_cache_lock = threading.Lock()
_llm_cache: Any | None = None


# ============== LLM 缓存 ==============


def get_llm_cache() -> Any:
    """获取全局 LLM InMemoryCache 单例

    首次调用时惰性创建 langchain_core.caches.InMemoryCache 实例。
    后续调用直接返回缓存的单例（线程安全）。
    """
    global _llm_cache  # noqa: PLW0603 - 模块级单例惰性初始化（双检锁模式）
    if _llm_cache is not None:
        return _llm_cache

    with _llm_cache_lock:
        if _llm_cache is not None:
            return _llm_cache

        try:
            from langchain_core.caches import InMemoryCache

            _llm_cache = InMemoryCache()
            logger.info("LLM InMemoryCache 已创建")
            return _llm_cache
        except ImportError:
            logger.warning("langchain_core.caches.InMemoryCache 不可用")
            return None
        except Exception as e:
            logger.warning(f"LLM Cache 创建失败: {e}")
            return None


def setup_llm_cache() -> None:
    """将 InMemoryCache 安装到 langchain_core 全局缓存槽

    优先使用 langchain_core.globals.set_llm_cache（新版 API），
    不可用时回退到 langchain_core.llm_cache（旧版属性）。
    """
    try:
        from langchain_core.globals import set_llm_cache

        cache = get_llm_cache()
        if cache is not None:
            set_llm_cache(cache)
            logger.info("全局 LLM Cache 已设置 (via langchain_core.globals)")
    except ImportError:
        try:
            import langchain_core

            cache = get_llm_cache()
            if cache is not None:
                langchain_core.llm_cache = cache  # type: ignore[attr-defined]  # langchain compat: llm_cache module attr not in stubs
                logger.info("全局 LLM Cache 已设置 (via langchain_core.llm_cache, 兼容模式)")
        except Exception as e:
            logger.warning(f"设置全局 LLM Cache 失败: {e}")
    except Exception as e:
        logger.warning(f"设置全局 LLM Cache 失败: {e}")


def setup_semantic_cache(
    redis_url: str | None = None,
    embedding_model: str | None = None,
) -> None:
    """设置 Redis 语义缓存（基于 embedding 相似度命中）

    语义缓存允许"问题相似但不完全相同"的查询命中缓存。
    需要配置 Redis + embedding 模型，否则回退到 InMemoryCache。

    Args:
        redis_url: Redis 连接字符串，默认从 settings.redis_url 读取
        embedding_model: embedding 模型名，默认从 settings.embedding_model 读取
    """
    try:
        from langchain_community.cache import RedisSemanticCache
        from langchain_core.globals import set_llm_cache

        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

        redis: str = redis_url or getattr(settings, "redis_url", "redis://127.0.0.1:6379/5")
        model_name = embedding_model or getattr(settings, "embedding_model", "text-embedding-3-small")
        embeddings = get_embeddings(model=model_name, use_cache=False)

        semantic_cache = RedisSemanticCache(
            redis_url=redis,
            embedding=embeddings,
        )
        set_llm_cache(semantic_cache)
        logger.info(f"语义缓存已设置 (redis={redis}, model={model_name})")
    except ImportError:
        try:
            import langchain_core
            from langchain_community.cache import RedisSemanticCache

            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

            redis: str = redis_url or getattr(settings, "redis_url", "redis://127.0.0.1:6379/5")
            model_name = embedding_model or getattr(settings, "embedding_model", "text-embedding-3-small")
            embeddings = get_embeddings(model=model_name, use_cache=False)

            langchain_core.llm_cache = RedisSemanticCache(  # type: ignore[attr-defined]  # langchain compat: llm_cache module attr not in stubs
                redis_url=redis,
                embedding=embeddings,
            )
            logger.info(f"语义缓存已设置 (兼容模式, redis={redis}, model={model_name})")
        except ImportError:
            logger.warning(
                "langchain_community.cache.RedisSemanticCache 不可用，请安装: pip install langchain-community redis"
            )
        except Exception as e:
            logger.warning(f"设置语义缓存失败: {e}，回退到 InMemoryCache")
            setup_llm_cache()
    except Exception as e:
        logger.warning(f"设置语义缓存失败: {e}，回退到 InMemoryCache")
        setup_llm_cache()


# ============== 速率限制器 ==============


def get_rate_limiter() -> Any:
    """获取全局 InMemoryRateLimiter 单例

    首次调用时根据 settings 创建：
    - requests_per_second: 每秒请求数（默认 1）
    - requests_per_minute: 桶大小（默认 60）
    - max_concurrency: 最大并发数（默认 10，仅用于日志）

    返回 None 表示速率限制不可用（langchain-core 版本过低）。
    """
    global _rate_limiter  # noqa: PLW0603 - 模块级单例惰性初始化（双检锁模式）
    if _rate_limiter is not None:
        return _rate_limiter

    with _rate_limiter_lock:
        if _rate_limiter is not None:
            return _rate_limiter

        try:
            from langchain_core.rate_limiters import InMemoryRateLimiter

            requests_per_minute = getattr(settings, "rate_limit_rpm", 60) or 60
            requests_per_second = getattr(settings, "rate_limit_rps", 1) or 1
            max_concurrency = getattr(settings, "rate_limit_max_concurrency", 10) or 10

            _rate_limiter = InMemoryRateLimiter(
                requests_per_second=requests_per_second,
                check_every_n_seconds=0.1,
                max_bucket_size=requests_per_minute,
            )

            logger.info(
                f"速率限制器已创建: {requests_per_second} req/s, "
                f"bucket={requests_per_minute}, max_concurrency={max_concurrency}"
            )
            return _rate_limiter
        except ImportError:
            logger.warning("langchain_core.rate_limiters.InMemoryRateLimiter 不可用，请升级 langchain-core>=0.3.0")
            return None
        except Exception as e:
            logger.warning(f"速率限制器创建失败: {e}，将不使用速率限制")
            return None


# ============== 统一缓存封装 ==============


def cached_model_creation(
    cache_key: str,
    use_cache: bool,
    creation_func: Callable[[], BaseChatModel],
    error_context: str = "",
) -> BaseChatModel:
    """统一的模型缓存逻辑封装

    将缓存查找 → 模型创建 → 缓存写入的通用流程抽取为单一函数，
    消除 _create_single_chat_model 和 get_chat_model_by_provider 中的重复代码。

    Args:
        cache_key: 缓存键
        use_cache: 是否启用缓存
        creation_func: 模型创建函数，返回 BaseChatModel 实例
        error_context: 错误日志的上下文信息（如 provider_id）

    Returns:
        缓存的或新创建的模型实例

    Raises:
        Exception: 模型创建失败时向上传播（调用方决定如何处理）
    """
    if use_cache:
        cached = get_cached_model(cache_key)
        if cached is not None:
            return cached

    try:
        model = creation_func()

        if use_cache:
            set_cached_model(cache_key, model)

        return model
    except Exception:
        ctx = f" ({error_context})" if error_context else ""
        logger.exception(f"模型创建失败{ctx}")
        raise
