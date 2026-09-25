from langchain_core.vectorstores import VectorStore

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def search_vector_store(
    vector_store: VectorStore,
    query: str,
    k: int = 4,
    score_threshold: float | None = None,
) -> list[tuple]:
    logger.info(f"搜索向量库: query='{query[:50]}...', k={k}")

    try:
        results = vector_store.similarity_search_with_score(
            query=query,
            k=k,
        )

        if score_threshold is not None:
            results = [(doc, score) for doc, score in results if score >= score_threshold]

        logger.info(f"找到 {len(results)} 个相关文档")
        return results

    except Exception:
        logger.exception("搜索失败")
        raise


# 向量存储后端抽象层
from .base import VectorStoreBackend
from .chroma_backend import ChromaBackend
from .faiss_backend import FAISSBackend
from .inmemory_backend import InMemoryBackend
from .milvus_backend import MilvusBackend
from .pgvector_backend import PGVectorBackend
from .pgvector_runtime import (
    reset_pgvector_cache,
    warm_up_pgvector_runtime,
)
from .registry import VectorStoreRegistry

__all__ = [
    # 向量存储后端
    "ChromaBackend",
    "FAISSBackend",
    "InMemoryBackend",
    "MilvusBackend",
    "PGVectorBackend",
    # 新抽象层
    "VectorStoreBackend",
    "VectorStoreRegistry",
    # PGVector 运行时（预热/缓存重置）
    "reset_pgvector_cache",
    "search_vector_store",
    "warm_up_pgvector_runtime",
]
