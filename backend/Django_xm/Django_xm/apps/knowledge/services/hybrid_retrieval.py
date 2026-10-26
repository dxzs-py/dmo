"""Hybrid 召回（Phase 3）：向量（dense）+ BM25（sparse），RRF 融合。

Phase 3 变更（按评测结论统一实现）：
- sparse 通道统一为**应用层 BM25**（``bm25_index``：CJK 2-gram tokenizer + Okapi
  TF-IDF/长度归一），删除 pg_trgm 2-gram 子串匹配；
- 删除 ``has_trigram_index`` / trigram 探测缓存 / ``prepare_rag_indexes --trigram`` 依赖；
- BM25 索引不可用（集合为空 / PG 不可达 / 构建失败）→ 退化为纯向量，不阻断检索；
- 融合仍走 ``rag_retrieval.rrf_fuse``（B-2 单源），精排由外层 rerank 负责。

职责边界：本模块只做「召回与融合」，不做精排、不做阈值语义判断
（dense 的阈值与分数归一由 ``search_vector_store`` 统一内核完成）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import PrivateAttr

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class HybridVectorStoreRetriever(BaseRetriever):
    """dense + BM25 → RRF 融合的召回器（精排之前）。

    与 ``ThresholdVectorStoreRetriever`` 的关系：
    - 相同：都返回**未精排**的候选、都不暴露 ``vector_store`` 公共属性
      （避免 ``with_scores`` 鸭子判定二次检索）、都在精排前完成阈值/归一；
    - 不同：本类多一路 BM25 召回，并用 RRF 合并两路排名。

    ``write_score`` 语义与 Threshold 版本一致：rerank 开启时不写精排前分数，
    由 reranker 写 relevance_score。
    """

    _vector_store: Any = PrivateAttr()
    _collection_name: str = PrivateAttr()
    _k: int = PrivateAttr()
    _score_threshold: float | None = PrivateAttr()
    _score_semantics: str = PrivateAttr()
    _write_score: bool = PrivateAttr()
    _search_kwargs: dict = PrivateAttr()
    _dense_weight: float = PrivateAttr()
    _sparse_weight: float = PrivateAttr()

    def __init__(
        self,
        vector_store,
        *,
        collection_name: str,
        k: int,
        score_threshold: float | None = None,
        score_semantics: str = "similarity",
        write_score: bool = True,
        search_kwargs: dict | None = None,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
    ):
        super().__init__()
        self._vector_store = vector_store
        self._collection_name = collection_name
        self._k = k
        self._score_threshold = score_threshold
        self._score_semantics = score_semantics
        self._write_score = write_score
        self._search_kwargs = dict(search_kwargs or {})
        self._dense_weight = dense_weight
        self._sparse_weight = sparse_weight

    def _sparse_recall(self, query: str) -> list[Document]:
        """BM25 召回；索引不可用时返回空（调用方退化为纯向量）。"""
        from Django_xm.apps.knowledge.services.bm25_index import get_bm25_index

        index = get_bm25_index(self._collection_name)
        if index is None:
            logger.warning("BM25 索引不可用，hybrid 退化为纯向量召回")
            return []
        return index.search(query, self._k)

    def _get_relevant_documents(self, query: str, **kwargs: Any) -> list[Document]:
        from Django_xm.apps.knowledge.services.rag_retrieval import rrf_fuse
        from Django_xm.apps.knowledge.vector_store import search_vector_store

        dense_pairs = search_vector_store(
            self._vector_store,
            query,
            k=self._k,
            score_threshold=self._score_threshold,
            score_semantics=self._score_semantics,
            search_kwargs=self._search_kwargs,
        )
        ranked_lists: list[list[tuple[Document, float | None]]] = [
            [(doc, score) for doc, score in dense_pairs]
        ]
        weights = [self._dense_weight]

        if self._sparse_weight > 0:
            try:
                sparse_docs = self._sparse_recall(query)
                if sparse_docs:
                    ranked_lists.append([(doc, None) for doc in sparse_docs])
                    weights.append(self._sparse_weight)
            except Exception as e:
                logger.warning(f"hybrid BM25 召回失败，退化为纯向量: {e}")

        if len(ranked_lists) == 1:
            docs = [doc for doc, _score in dense_pairs][: self._k]
        else:
            fused = rrf_fuse(ranked_lists, weights=weights)
            docs = [doc for doc, _fused, _first_score, _first_idx in fused][: self._k]

        if self._write_score:
            dense_scores = {id(doc): score for doc, score in dense_pairs}
            for doc in docs:
                score = dense_scores.get(id(doc))
                if score is not None:
                    doc.metadata["score"] = float(score)

        logger.debug(
            f"hybrid 召回: dense={len(dense_pairs)}, "
            f"sparse={'on' if len(ranked_lists) > 1 else 'off'}(bm25), out={len(docs)}"
        )
        return docs

    async def _aget_relevant_documents(self, query: str, **kwargs: Any) -> list[Document]:
        return await asyncio.to_thread(self._get_relevant_documents, query, **kwargs)
