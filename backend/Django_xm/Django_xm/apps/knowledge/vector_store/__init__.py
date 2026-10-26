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


# 后端分数的合法语义值（C1/KV-2 契约）
VALID_SCORE_SEMANTICS = ("similarity", "distance")


def resolve_score_semantics(vector_store, default: str = "similarity") -> str:
    """解析向量库实例声明的原生分数语义（C-2arch 单源）。

    后端在 store 实例上烙印 ``native_score_semantics``（pgvector=distance，
    其余=similarity，见 VectorStoreBackend._stamp_score_semantics）。

    白名单归一的原因：测试与第三方实现常用 MagicMock 代替 store，
    ``getattr`` 会返回子 mock 而非字符串——直接透传会让 search_vector_store
    的非法语义校验抛 ValueError（测试环境失败，R5-4）。缺失或非法一律回退
    default。

    Args:
        vector_store: 向量库实例
        default: 声明缺失/非法时的回退语义

    Returns:
        "similarity" 或 "distance"
    """
    semantics = getattr(vector_store, "native_score_semantics", default)
    return semantics if semantics in VALID_SCORE_SEMANTICS else default


def search_vector_store(
    vector_store: VectorStore,
    query: str,
    k: int = 4,
    score_threshold: float | None = None,
    query_embedding: list[float] | None = None,
    *,
    score_semantics: str = "similarity",
    search_kwargs: dict | None = None,
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
        search_kwargs: 额外透传到底层检索的 kwargs（如 metadata filter）；
            k 由本函数独立管理，同名字段被忽略

    Returns:
        [(Document, score)]，score 为相似度语义（None 保留），相关度降序
    """
    if score_semantics not in VALID_SCORE_SEMANTICS:
        raise ValueError(f"未知 score_semantics: {score_semantics!r}")

    extra = {key: value for key, value in (search_kwargs or {}).items() if key != "k"}

    logger.info(f"搜索向量库: query='{query[:50]}...', k={k}")

    try:
        if query_embedding is not None:
            results = vector_store.similarity_search_with_score_by_vector(
                query_embedding,
                k=k,
                **extra,
            )
        else:
            results = vector_store.similarity_search_with_score(
                query=query,
                k=k,
                **extra,
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
    "VALID_SCORE_SEMANTICS",
    # 向量存储后端
    "ChromaBackend",
    "FAISSBackend",
    "InMemoryBackend",
    "MilvusBackend",
    "PGVectorBackend",
    # 新抽象层
    "VectorStoreBackend",
    "VectorStoreRegistry",
    # 公共工具
    "dispose_vector_stores",
    # PGVector 运行时（预热/缓存重置）
    "reset_pgvector_cache",
    "resolve_score_semantics",
    "search_vector_store",
    "warm_up_pgvector_runtime",
]
