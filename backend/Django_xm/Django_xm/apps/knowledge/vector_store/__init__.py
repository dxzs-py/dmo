from langchain_core.vectorstores import VectorStore

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def dispose_vector_stores(stores) -> int:
    """释放一批 VectorStore 实例的底层 engine 连接池（C3/KV-3）。

    `vector_store._engine` 是 LangChain PGVector 的内部属性，无公开释放接口；
    库升级后可能改名，届时需改为官方 API。单实例释放失败不影响其余。

    Returns:
        成功 dispose 的实例数
    """
    disposed = 0
    for store in stores:
        engine = getattr(store, "_engine", None)
        if engine is None:
            continue
        try:
            engine.dispose()
            disposed += 1
        except Exception:  # noqa: S110  # cleanup, 单个 engine 释放失败不影响其他
            pass
    return disposed


def search_vector_store(
    vector_store: VectorStore,
    query: str,
    k: int = 4,
    score_threshold: float | None = None,
    query_embedding: list[float] | None = None,
    *,
    score_semantics: str = "similarity",
) -> list[tuple]:
    """向量库打分检索。

    query_embedding 为 None 时由向量库自行 embed query（现行为）；
    提供预计算 embedding 时走 by-vector 检索（跨库聚合场景复用同一次 embed）。

    分数语义契约（C1/KV-2）：**本函数输出与 score_threshold 一律为相似度语义**
    （越大越相关，0~1 口径与 config 的 retriever_score_threshold/rag_score_threshold
    一致）。后端原生返回 cosine distance（langchain_postgres 0.0.17 实测，越小越
    相关，列表按 distance 升序 = 最相关在前）时，调用方必须传
    score_semantics="distance"，本函数按 similarity = 1 - distance 归一后再过滤
    ——此前直接 `score >= threshold` 是反向过滤（淘汰最相关文档），且
    views_rag 按 score 降序的库内 RRF 排名会反转（最差文档 rank=1）。

    Args:
        vector_store: 原始 VectorStore 实例
        query: 查询文本
        k: 返回数
        score_threshold: 相似度阈值（0~1），None 表示不过滤
        query_embedding: 预计算查询 embedding（可选）
        score_semantics: 后端原生分数语义，"similarity"（默认）或 "distance"

    Returns:
        [(Document, score)]，score 为相似度语义（None 保留），相关度降序
    """
    if score_semantics not in ("similarity", "distance"):
        raise ValueError(f"未知 score_semantics: {score_semantics!r}")

    logger.info(f"搜索向量库: query='{query[:50]}...', k={k}")

    try:
        if query_embedding is not None:
            results = vector_store.similarity_search_with_score_by_vector(
                query_embedding,
                k=k,
            )
        else:
            results = vector_store.similarity_search_with_score(
                query=query,
                k=k,
            )

        if score_semantics == "distance":
            # cosine distance → 相似度（similarity = 1 - distance）；None 分数保留
            results = [
                (doc, (1 - score) if score is not None else None) for doc, score in results
            ]

        if score_threshold is not None:
            results = [(doc, score) for doc, score in results if score is not None and score >= score_threshold]

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
    # 公共工具
    "dispose_vector_stores",
    # 新抽象层
    "VectorStoreBackend",
    "VectorStoreRegistry",
    # PGVector 运行时（预热/缓存重置）
    "reset_pgvector_cache",
    "search_vector_store",
    "warm_up_pgvector_runtime",
]
