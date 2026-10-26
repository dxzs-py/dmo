"""精排复用件（O3）：把「对一批已召回的文档做 cross-encoder 精排」抽成纯函数。

消费方：
- ``UnifiedRagPipeline._degradation_level1_from_retriever``（Level 1 扩展召回后精排）
- ``UnifiedRagPipeline.search_multi_kb``（跨库 RRF 融合后统一精排）
- ``views_rag.SearchAllView``（跨库聚合 API）

设计约束：
- 精排是**可插拔**环节：FlashRank 缺失 / 构造失败 / 推理异常一律回退 ``docs[:top_n]``，
  绝不向上抛异常（降级链的既有语义）；
- 分数闸门走 ``settings.rag_rerank_score_threshold``（默认 0=不过滤）。
  ``relevance_score`` 由 reranker 写入 metadata，不同模型校准不同，启用前需评测。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import PrivateAttr

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)


class _StaticDocRetriever(BaseRetriever):
    """把固定文档列表包装为 BaseRetriever（供 compression 检索器消费）。"""

    _docs: list[Document] = PrivateAttr()

    def __init__(self, documents: list[Document]):
        super().__init__()
        self._docs = list(documents)

    def _get_relevant_documents(self, query: str, **kwargs: Any) -> list[Document]:
        return self._docs

    async def _aget_relevant_documents(self, query: str, **kwargs: Any) -> list[Document]:
        return self._docs


def _relevance_score(doc: Document) -> float | None:
    """读取 reranker 写入的 relevance_score（numpy.float32 也能归一）。"""
    raw = doc.metadata.get("relevance_score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def rerank_documents(
    docs: list[Document],
    query: str,
    top_n: int,
    *,
    model: str | None = None,
    score_threshold: float | None = None,
) -> list[Document]:
    """对候选文档执行 cross-encoder 精排。

    Args:
        docs: 已召回的候选（按召回顺序，通常含较大窗口）
        query: 用户查询（用于精排打分）
        top_n: 精排后保留数
        model: 模型标识，None 时读 settings.rag_rerank_model
        score_threshold: relevance_score 下限；None 时读
            settings.rag_rerank_score_threshold（0=不过滤）

    Returns:
        精排后的文档列表（失败时回退为 docs[:top_n]）
    """
    candidates = list(docs or [])
    if not candidates:
        return []

    limit = max(int(top_n), 1)
    model_name = model or settings.rag_rerank_model
    threshold = (
        settings.rag_rerank_score_threshold if score_threshold is None else score_threshold
    )

    from Django_xm.apps.knowledge.services.retrieval_service import (
        ADVANCED_RETRIEVERS_AVAILABLE,
        FLASHRANK_AVAILABLE,
        get_flashrank_reranker,
    )

    if not (FLASHRANK_AVAILABLE and ADVANCED_RETRIEVERS_AVAILABLE):
        return candidates[:limit]

    try:
        from langchain_classic.retrievers import ContextualCompressionRetriever

        reranker = get_flashrank_reranker(model_name, limit)
        compression = ContextualCompressionRetriever(
            base_compressor=reranker,
            base_retriever=_StaticDocRetriever(candidates),
        )
        reranked = compression.invoke(query)
    except Exception as e:
        logger.warning(f"精排失败，回退到召回顺序: {e}")
        return candidates[:limit]

    if threshold and threshold > 0:
        reranked = [
            doc
            for doc in reranked
            if (_relevance_score(doc) is None or _relevance_score(doc) >= threshold)
        ]

    return reranked[:limit]


async def rerank_documents_async(
    docs: list[Document],
    query: str,
    top_n: int,
    *,
    model: str | None = None,
    score_threshold: float | None = None,
) -> list[Document]:
    """异步包装：精排为 CPU/ONNX 推理，经 to_thread 离开事件循环。"""
    return await asyncio.to_thread(
        rerank_documents,
        docs,
        query,
        top_n,
        model=model,
        score_threshold=score_threshold,
    )
