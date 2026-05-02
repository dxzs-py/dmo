"""
Embeddings 模块

提供统一的 Embedding 模型接口，用于将文本转换为向量。
集成 Redis 缓存，避免重复调用 Embedding API。
"""

from typing import Optional, List
from langchain_openai import OpenAIEmbeddings
from langchain_core.embeddings import Embeddings

import logging

logger = logging.getLogger(__name__)


class CachedEmbeddings(Embeddings):
    """带 Redis 缓存的 Embeddings 包装器"""

    def __init__(self, embeddings: Embeddings, model: str = "default"):
        self._embeddings = embeddings
        self._model = model
        self._hit = 0
        self._miss = 0

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        from Django_xm.apps.ai_engine.services.cache_service import (
            VectorSearchCacheService
        )

        results = []
        uncached_texts = []
        uncached_indices = []

        for i, text in enumerate(texts):
            cached = VectorSearchCacheService.get_cached_embedding(text, self._model)
            if cached is not None:
                results.append(cached)
                self._hit += 1
            else:
                results.append(None)
                uncached_texts.append(text)
                uncached_indices.append(i)
                self._miss += 1

        if uncached_texts:
            new_vectors = self._embeddings.embed_documents(uncached_texts)
            for idx, text, vector in zip(uncached_indices, uncached_texts, new_vectors):
                results[idx] = vector
                VectorSearchCacheService.cache_embedding(text, vector, self._model)

        if self._hit + self._miss > 0 and (self._hit + self._miss) % 100 == 0:
            total = self._hit + self._miss
            logger.info(
                f"Embedding 缓存统计: 命中={self._hit}, 未命中={self._miss}, "
                f"命中率={self._hit / total * 100:.1f}%"
            )

        return results

    def embed_query(self, text: str) -> List[float]:
        from Django_xm.apps.ai_engine.services.cache_service import (
            VectorSearchCacheService
        )

        cached = VectorSearchCacheService.get_cached_embedding(text, self._model)
        if cached is not None:
            self._hit += 1
            return cached

        self._miss += 1
        vector = self._embeddings.embed_query(text)
        VectorSearchCacheService.cache_embedding(text, vector, self._model)
        return vector

    @property
    def cache_stats(self):
        total = self._hit + self._miss
        return {
            'hit': self._hit,
            'miss': self._miss,
            'total': total,
            'hit_rate': round(self._hit / total * 100, 2) if total > 0 else 0,
        }


def get_openai_api_key() -> Optional[str]:
    try:
        from Django_xm.apps.ai_engine.config import settings
        return getattr(settings, 'openai_api_key', None)
    except ImportError:
        import os
        return os.environ.get("OPENAI_API_KEY")


def get_openai_api_base() -> str:
    try:
        from Django_xm.apps.ai_engine.config import settings
        return getattr(settings, 'openai_api_base', "https://api.openai.com/v1")
    except ImportError:
        import os
        return os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")


def get_embedding_model() -> str:
    try:
        from Django_xm.apps.ai_engine.config import settings
        return getattr(settings, 'embedding_model', "text-embedding-3-small")
    except ImportError:
        import os
        return os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")


def get_embedding_batch_size() -> int:
    try:
        from Django_xm.apps.ai_engine.config import settings
        return getattr(settings, 'embedding_batch_size', 10)
    except ImportError:
        import os
        return int(os.environ.get("EMBEDDING_BATCH_SIZE", "10"))


def get_embeddings(
    model: Optional[str] = None,
    batch_size: Optional[int] = None,
    use_cache: bool = True,
    **kwargs,
) -> Embeddings:
    model = model or get_embedding_model()
    batch_size = batch_size or get_embedding_batch_size()

    logger.info(f"🔢 创建 Embedding 模型: {model}, batch_size={batch_size}, cache={use_cache}")

    try:
        embeddings = OpenAIEmbeddings(
            model=model,
            api_key=get_openai_api_key(),
            base_url=get_openai_api_base(),
            chunk_size=batch_size,
            timeout=60.0,
            max_retries=3,
            **kwargs,
        )

        if use_cache:
            embeddings = CachedEmbeddings(embeddings, model=model)

        logger.debug("✅ Embedding 模型创建成功")
        return embeddings

    except Exception as e:
        logger.error(f"❌ Embedding 模型创建失败: {e}")
        raise


def get_embedding_dimension(model: Optional[str] = None) -> int:
    """获取 Embedding 模型的向量维度"""
    model = model or get_embedding_model()

    dimensions = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1538,
    }

    if model not in dimensions:
        logger.warning(f"未知的模型维度: {model}，返回默认值 1536")
        return 1536

    return dimensions[model]


def estimate_embedding_cost(
    num_tokens: int,
    model: Optional[str] = None,
) -> float:
    """估算 Embedding 成本（美元）"""
    model = model or get_embedding_model()

    pricing = {
        "text-embedding-3-small": 0.02,
        "text-embedding-3-large": 0.13,
        "text-embedding-ada-002": 0.10,
    }

    if model not in pricing:
        price_per_million = 0.02
    else:
        price_per_million = pricing[model]

    cost = (num_tokens / 1_000_000) * price_per_million

    logger.info(
        f"💰 Embedding 成本估算: "
        f"{num_tokens:,} tokens × ${price_per_million}/M = ${cost:.4f}"
    )

    return cost


def test_embeddings(
    model: Optional[str] = None,
    test_text: str = "这是一个测试文本",
) -> bool:
    """测试 Embedding 模型是否正常工作"""
    try:
        logger.info("🧪 测试 Embedding 模型...")

        embeddings = get_embeddings(model=model)

        vector = embeddings.embed_query(test_text)
        logger.info(f"   单文本嵌入: 维度={len(vector)}")

        texts = [test_text, test_text + " 2", test_text + " 3"]
        vectors = embeddings.embed_documents(texts)
        logger.info(f"   批量嵌入: {len(vectors)} 个向量")

        logger.info("✅ Embedding 模型测试通过")
        return True

    except Exception as e:
        logger.error(f"❌ Embedding 模型测试失败: {e}")
        return False


EMBEDDING_CONFIGS = {
    "fast": {
        "model": "text-embedding-3-small",
        "description": "快速模型，适合开发和测试",
    },
    "quality": {
        "model": "text-embedding-3-large",
        "description": "高质量模型，适合生产环境",
    },
    "legacy": {
        "model": "text-embedding-ada-002",
        "description": "旧版模型（不推荐）",
    },
}


def get_embeddings_by_preset(
    preset: str = "fast",
    **kwargs,
) -> Embeddings:
    """根据预设配置获取 Embedding 模型"""
    if preset not in EMBEDDING_CONFIGS:
        available = ", ".join(EMBEDDING_CONFIGS.keys())
        raise ValueError(
            f"未知的预设: {preset}. 可用预设: {available}"
        )

    config = EMBEDDING_CONFIGS[preset].copy()
    model_name = config.pop("model")
    config.update(kwargs)

    logger.info(f"使用 Embedding 预设: {preset} (model={model_name})")
    return get_embeddings(model=model_name, **config)