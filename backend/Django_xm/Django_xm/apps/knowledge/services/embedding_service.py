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
import time

from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 100
# 批间延迟：embedding provider 限速保护（RP-11 调查结论——非轮询等待残留，
# agent 审计 perf B 组已判非缺陷；消费方为 Celery 任务/线程池上下文）。
# 0.5s→0.2s：批 100 条下上限 500 条/s，兼顾限速保护与 Celery soft_time_limit
# 1500s 预算（10 万 chunk 场景固定等待从 ~50s 降到 ~20s）。
EMBEDDING_BATCH_DELAY = 0.2


# ============== CachedEmbeddings 保留在本模块（与 cache_manager 耦合） ==============


class CachedEmbeddings(Embeddings):
    """带 Redis 缓存的 Embeddings 包装器

    实现层依赖 Django_xm.apps.cache_manager.services.cache_service.VectorSearchCacheService
    保留在 knowledge.services.embedding_service 模块以避免循环依赖
    （embedding_factory 在 ai_engine 中，不能依赖 cache_manager）
    """

    def __init__(self, embeddings: Embeddings, model: str = "default", dimension: int | None = None):
        self._embeddings = embeddings
        self._model = model
        self._dimension = dimension
        self._hit = 0
        self._miss = 0

    def _cache_model(self) -> str:
        """缓存键命名空间 = 活跃 provider id + 显式维度段（RP-06）。

        键结构原为 ``embedding:{model}:{text_hash}`` 且 model 实参恒 "default"——
        与实际 provider/维度无关，切换 embedding provider 后最长 7 天返回陈旧向量。
        修复后每次调用时动态解析活跃 provider id（fallback 切换后随
        get_active_provider_id 立即生效，写读均隔离到新命名空间）；裸 Embeddings
        （无 provider 感知）回退注入的 model。维度段仅在显式 required_dimension
        时附加（provider 内 MRL 截断维度变化场景由 settings_views 切换清键兜底）。
        """
        provider_id = None
        getter = getattr(self._embeddings, "get_active_provider_id", None)
        if getter is not None:
            provider_id = getter()
        parts = [provider_id or self._model]
        if self._dimension:
            parts.append(f"d{self._dimension}")
        return ":".join(parts)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        from Django_xm.apps.cache_manager.services.cache_service import VectorSearchCacheService

        cache_model = self._cache_model()
        results: list[list[float] | None] = [None] * len(texts)
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []

        # RP-11：批量读缓存（单次 Redis 往返），替代原逐条 GET
        cached_map = VectorSearchCacheService.get_cached_embeddings_batch(texts, cache_model)
        for i, text in enumerate(texts):
            cached = cached_map.get(text)
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
                    # provider 限速保护（见 EMBEDDING_BATCH_DELAY 注释）
                    time.sleep(EMBEDDING_BATCH_DELAY)

            # RP-11：批量写缓存（单次 Redis 往返），替代原逐条 SET
            VectorSearchCacheService.cache_embeddings_batch(
                list(zip(uncached_texts, all_new_vectors, strict=False)), cache_model
            )
            for idx, vector in zip(uncached_indices, all_new_vectors, strict=False):
                results[idx] = vector

        if self._hit + self._miss > 0 and (self._hit + self._miss) % 100 == 0:
            total = self._hit + self._miss
            logger.info(
                f"Embedding 缓存统计: 命中={self._hit}, 未命中={self._miss}, 命中率={self._hit / total * 100:.1f}%"
            )

        return results

    def embed_query(self, text: str) -> list[float]:
        from Django_xm.apps.cache_manager.services.cache_service import VectorSearchCacheService

        cache_model = self._cache_model()
        cached = VectorSearchCacheService.get_cached_embedding(text, cache_model)
        if cached is not None:
            self._hit += 1
            return cached

        self._miss += 1
        vector = self._embeddings.embed_query(text)
        VectorSearchCacheService.cache_embedding(text, vector, cache_model)
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

        cache_model = self._cache_model()
        results: list[list[float] | None] = [None] * len(texts)
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []

        # RP-11：批量读缓存（单次 Redis 往返，与 sync 版同构；Redis 为同步 IO，
        # 与原实现逐条同步 GET 形态一致，不引入新的阻塞面变化）
        cached_map = VectorSearchCacheService.get_cached_embeddings_batch(texts, cache_model)
        for i, text in enumerate(texts):
            cached = cached_map.get(text)
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
                    # provider 限速保护（见 EMBEDDING_BATCH_DELAY 注释）
                    await asyncio.sleep(EMBEDDING_BATCH_DELAY)

            # RP-11：批量写缓存（单次 Redis 往返）
            VectorSearchCacheService.cache_embeddings_batch(
                list(zip(uncached_texts, all_new_vectors, strict=False)), cache_model
            )
            for idx, vector in zip(uncached_indices, all_new_vectors, strict=False):
                results[idx] = vector

        if self._hit + self._miss > 0 and (self._hit + self._miss) % 100 == 0:
            total = self._hit + self._miss
            logger.info(
                f"Embedding 缓存统计: 命中={self._hit}, 未命中={self._miss}, 命中率={self._hit / total * 100:.1f}%"
            )

        return results

    async def aembed_query(self, text: str) -> list[float]:
        from Django_xm.apps.cache_manager.services.cache_service import VectorSearchCacheService

        cache_model = self._cache_model()
        cached = VectorSearchCacheService.get_cached_embedding(text, cache_model)
        if cached is not None:
            self._hit += 1
            return cached

        self._miss += 1
        vector = await self._embeddings.aembed_query(text)
        VectorSearchCacheService.cache_embedding(text, vector, cache_model)
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
        # 缓存包装器由调用方注入（ARCH-03 依赖倒置）：CachedEmbeddings 保留在
        # knowledge 侧，ai_engine 工厂不感知本模块实现。
        # RP-06：键命名空间由 CachedEmbeddings 动态构造（活跃 provider id + 维度段）
        cache_wrapper_factory=lambda emb: CachedEmbeddings(
            emb, model=model or "default", dimension=required_dimension
        ),
        **kwargs,
    )


def collect_fallback_info(embeddings) -> dict | None:
    """读取 FallbackEmbedding 降级事件并把 SystemConfig 对齐实际 provider（B-5 收口）。

    "Embedding 降级 → SystemConfig.embedding_provider 自动同步"的配置管理职责
    单源收口在本模块（此前内嵌在 kb_service 上传编排里，职责错位）；调用方
    （上传链/未来的其他消费点）只透传返回的 fallback_info，不感知配置写入。

    语义与原 kb_service 内联块一致：
    - 实例无降级事件接口（裸 Embeddings）或无降级事件 → 返回 None，不触碰配置；
    - 有事件且有实际 provider 时读改写 SystemConfig.embedding_provider（幂等：
      provider 已一致则不写），写入失败仅 warning 不中断业务流程。

    Returns:
        None 或 {"events": [...], "actual_provider": str}
    """
    if not (hasattr(embeddings, "get_fallback_events") and hasattr(embeddings, "get_active_provider_id")):
        return None
    events = embeddings.get_fallback_events()
    if not events:
        return None
    actual_provider = embeddings.get_active_provider_id()
    if actual_provider:
        try:
            from Django_xm.apps.knowledge.config import SystemConfig

            current_config = SystemConfig.get_value("embedding_provider", {})
            if current_config.get("provider_id") != actual_provider:
                current_config["provider_id"] = actual_provider
                SystemConfig.set_value("embedding_provider", current_config)
                logger.info(f"Embedding 降级：SystemConfig embedding_provider 已更新为 {actual_provider}")
        except Exception as e:
            logger.warning(f"Embedding 降级：更新 SystemConfig 失败: {e}")
    return {"events": events, "actual_provider": actual_provider}


def get_embedding_dimension(model: str | None = None) -> int:
    """获取 Embedding 维度（委托 embedding_factory）

    通过创建 Embeddings 实例并调用 detect_embedding_dimension 探测实际维度，
    替代原有的硬编码映射表。

    E5-12 兜底说明：探测失败时返回 1536（OpenAI text-embedding ada/3-small
    家族默认维度，本项目历史默认）。曾评估以活跃 provider 的已知维度
    （EmbeddingProviderConfig.dimension）替换字面量，但"活跃 provider id →
    配置行"的映射需复刻 embedding_factory 的解析链，且探测失败场景（provider
    配置已异常）再引入 DB 查询会新增失败面——按"不引入新风险"保留字面量；
    失败路径已有 warning 日志（下except），可观测性达标。
    """
    try:
        embeddings = get_embeddings(model=model, use_cache=False)
        return detect_embedding_dimension(embeddings)
    except Exception as e:
        logger.warning(f"探测 Embedding 维度失败: {e}，返回默认值 1536")
        return 1536
