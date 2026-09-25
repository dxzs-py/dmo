"""
Embeddings 模块 - 薄壳层（thin wrapper）

实际创建逻辑已迁移至 ai_engine.services.embedding_factory，
本文件保留仍被直接引用的接口：CachedEmbeddings（与 cache_manager 耦合）、
get_embeddings、get_embedding_dimension。

新的模块应该直接使用：
    from Django_xm.apps.ai_engine.services.embedding_factory import (
        get_embeddings_with_fallback,
        detect_embedding_dimension,
    )
"""

import asyncio
import logging
from typing import Any

from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 100
EMBEDDING_BATCH_DELAY = 0.5


# ============== CachedEmbeddings 保留在本模块（与 cache_manager 耦合） ==============


class CachedEmbeddings(Embeddings):
    """带 Redis 缓存的 Embeddings 包装器

    实现层依赖 Django_xm.apps.cache_manager.services.cache_service.VectorSearchCacheService
    保留在 knowledge.services.embedding_service 模块以避免循环依赖
    （embedding_factory 在 ai_engine 中，不能依赖 cache_manager）
    """

    def __init__(self, embeddings: Embeddings, model: str = "default"):
        self._embeddings = embeddings
        self._model = model
        self._hit = 0
        self._miss = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        from Django_xm.apps.cache_manager.services.cache_service import VectorSearchCacheService

        results: list[list[float] | None] = [None] * len(texts)
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []

        for i, text in enumerate(texts):
            cached = VectorSearchCacheService.get_cached_embedding(text, self._model)
            if cached is not None:
                results[i] = cached
                self._hit += 1
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)
                self._miss += 1

        if uncached_texts:
            all_new_vectors: list[list[float]] = []
            for batch_start in range(0, len(uncached_texts), EMBEDDING_BATCH_SIZE):
                batch = uncached_texts[batch_start : batch_start + EMBEDDING_BATCH_SIZE]
                batch_vectors = self._embeddings.embed_documents(batch)
                all_new_vectors.extend(batch_vectors)
                if batch_start + EMBEDDING_BATCH_SIZE < len(uncached_texts):
                    import time

                    time.sleep(EMBEDDING_BATCH_DELAY)

            for idx, text, vector in zip(uncached_indices, uncached_texts, all_new_vectors, strict=False):
                results[idx] = vector
                VectorSearchCacheService.cache_embedding(text, vector, self._model)

        if self._hit + self._miss > 0 and (self._hit + self._miss) % 100 == 0:
            total = self._hit + self._miss
            logger.info(
                f"Embedding 缓存统计: 命中={self._hit}, 未命中={self._miss}, 命中率={self._hit / total * 100:.1f}%"
            )

        return results

    def embed_query(self, text: str) -> list[float]:
        from Django_xm.apps.cache_manager.services.cache_service import VectorSearchCacheService

        cached = VectorSearchCacheService.get_cached_embedding(text, self._model)
        if cached is not None:
            self._hit += 1
            return cached

        self._miss += 1
        vector = self._embeddings.embed_query(text)
        VectorSearchCacheService.cache_embedding(text, vector, self._model)
        return vector

    # 透传 FallbackEmbedding 的降级检测方法
    def get_fallback_events(self):
        return self._embeddings.get_fallback_events() if hasattr(self._embeddings, "get_fallback_events") else []

    def get_active_provider_id(self):
        return (
            self._embeddings.get_active_provider_id() if hasattr(self._embeddings, "get_active_provider_id") else None
        )

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        from Django_xm.apps.cache_manager.services.cache_service import VectorSearchCacheService

        results: list[list[float] | None] = [None] * len(texts)
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []

        for i, text in enumerate(texts):
            cached = VectorSearchCacheService.get_cached_embedding(text, self._model)
            if cached is not None:
                results[i] = cached
                self._hit += 1
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)
                self._miss += 1

        if uncached_texts:
            all_new_vectors: list[list[float]] = []
            for batch_start in range(0, len(uncached_texts), EMBEDDING_BATCH_SIZE):
                batch = uncached_texts[batch_start : batch_start + EMBEDDING_BATCH_SIZE]
                batch_vectors = await self._embeddings.aembed_documents(batch)
                all_new_vectors.extend(batch_vectors)
                if batch_start + EMBEDDING_BATCH_SIZE < len(uncached_texts):
                    await asyncio.sleep(EMBEDDING_BATCH_DELAY)

            for idx, text, vector in zip(uncached_indices, uncached_texts, all_new_vectors, strict=False):
                results[idx] = vector
                VectorSearchCacheService.cache_embedding(text, vector, self._model)

        if self._hit + self._miss > 0 and (self._hit + self._miss) % 100 == 0:
            total = self._hit + self._miss
            logger.info(
                f"Embedding 缓存统计: 命中={self._hit}, 未命中={self._miss}, 命中率={self._hit / total * 100:.1f}%"
            )

        return results

    async def aembed_query(self, text: str) -> list[float]:
        from Django_xm.apps.cache_manager.services.cache_service import VectorSearchCacheService

        cached = VectorSearchCacheService.get_cached_embedding(text, self._model)
        if cached is not None:
            self._hit += 1
            return cached

        self._miss += 1
        vector = await self._embeddings.aembed_query(text)
        VectorSearchCacheService.cache_embedding(text, vector, self._model)
        return vector

    @property
    def cache_stats(self):
        total = self._hit + self._miss
        return {
            "hit": self._hit,
            "miss": self._miss,
            "total": total,
            "hit_rate": round(self._hit / total * 100, 2) if total > 0 else 0,
        }


# ============== 兼容层 API：委托 ai_engine.services.embedding_factory ==============

from Django_xm.apps.knowledge.config import (
    detect_embedding_dimension,
    get_embeddings_with_fallback,
)


def get_embeddings(
    model: str | None = None,
    batch_size: int | None = None,
    use_cache: bool = True,
    use_fallback: bool = True,
    preferred_provider: str | None = None,
    required_dimension: int | None = None,
    **kwargs,
) -> Embeddings:
    """获取 Embeddings 实例（委托 embedding_factory）

    preferred_provider 优先级：
    1. 调用方显式传入
    2. SystemConfig 数据库配置
    3. .env 默认配置
    """
    from Django_xm.apps.knowledge.config import (
        get_system_embedding_provider,
        settings,
    )

    effective_provider = preferred_provider
    if effective_provider is None:
        system_provider = get_system_embedding_provider()
        if system_provider:
            effective_provider = system_provider

    return get_embeddings_with_fallback(
        model=model,
        batch_size=batch_size or settings.embedding_batch_size,
        use_cache=use_cache,
        use_fallback=use_fallback,
        preferred_provider=effective_provider,
        required_dimension=required_dimension,
        **kwargs,
    )


def get_embedding_dimension(model: str | None = None) -> int:
    """获取 Embedding 维度（委托 embedding_factory）

    通过创建 Embeddings 实例并调用 detect_embedding_dimension 探测实际维度，
    替代原有的硬编码映射表。
    """
    try:
        embeddings = get_embeddings(model=model, use_cache=False)
        return detect_embedding_dimension(embeddings)
    except Exception as e:
        logger.warning(f"探测 Embedding 维度失败: {e}，返回默认值 1536")
        return 1536
