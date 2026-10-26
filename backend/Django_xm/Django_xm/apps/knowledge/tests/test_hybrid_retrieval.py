"""Hybrid 召回（dense + BM25 → RRF）回归。

Phase 3：sparse 通道统一 BM25，pg_trgm 2-gram 已删除。

锁定：
1. BM25 命中与 dense 结果经 ``rrf_fuse`` 融合，两路文档都保留；
2. BM25 索引不可用 / 抛异常 → 静默退化为纯向量；
3. 未启用 rerank 时写归一 similarity 供 sources 透出；启用时不写；
4. ``create_retriever`` 仅在 PGVector（collection_name 为 str）且开关开启时选 Hybrid。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_hybrid_retrieval --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services import retrieval_service
from Django_xm.apps.knowledge.services.hybrid_retrieval import HybridVectorStoreRetriever
from Django_xm.apps.knowledge.services.retrieval_service import ThresholdVectorStoreRetriever

_BM25 = "Django_xm.apps.knowledge.services.bm25_index"


def _doc(content: str, source: str) -> Document:
    return Document(page_content=content, metadata={"source": source, "chunk_index": 0})


def _retriever(store, **kwargs):
    params = {
        "collection_name": "user_1_kb",
        "k": 10,
        "score_threshold": 0.35,
        "score_semantics": "similarity",
        "write_score": True,
        "dense_weight": 0.7,
        "sparse_weight": 0.3,
    }
    params.update(kwargs)
    return HybridVectorStoreRetriever(store, **params)


class HybridRecallTests(unittest.TestCase):
    """Hybrid 召回：融合 / BM25 不可用 / BM25 异常 / 分数写入。"""

    def _store(self, pairs):
        store = mock.MagicMock(name="vector_store")
        store.similarity_search_with_score.return_value = pairs
        return store

    def test_fuses_dense_and_bm25(self):
        dense = _doc("D", "d.md")
        sparse = _doc("S", "s.md")
        store = self._store([(dense, 0.9)])
        index = mock.MagicMock(name="bm25_index")
        index.search.return_value = [sparse]

        with mock.patch(f"{_BM25}.get_bm25_index", return_value=index):
            docs = _retriever(store).invoke("查询")

        index.search.assert_called_once()
        self.assertEqual({doc.page_content for doc in docs}, {"D", "S"})
        # 未开启 rerank → dense 命中写归一 similarity；BM25-only 文档无分数
        self.assertEqual(dense.metadata["score"], 0.9)
        self.assertNotIn("score", sparse.metadata)

    def test_bm25_unavailable_degrades_to_dense(self):
        dense = _doc("D", "d.md")
        store = self._store([(dense, 0.9)])

        with mock.patch(f"{_BM25}.get_bm25_index", return_value=None):
            docs = _retriever(store).invoke("查询")

        self.assertEqual([doc.page_content for doc in docs], ["D"])

    def test_bm25_exception_degrades_to_dense(self):
        dense = _doc("D", "d.md")
        store = self._store([(dense, 0.9)])

        with mock.patch(f"{_BM25}.get_bm25_index", side_effect=RuntimeError("pg down")):
            docs = _retriever(store).invoke("查询")

        self.assertEqual([doc.page_content for doc in docs], ["D"])

    def test_writes_no_score_when_rerank_enabled(self):
        dense = _doc("D", "d.md")
        store = self._store([(dense, 0.9)])

        with mock.patch(f"{_BM25}.get_bm25_index", return_value=None):
            docs = _retriever(store, write_score=False).invoke("查询")

        self.assertNotIn("score", docs[0].metadata)


class HybridSelectionTests(unittest.TestCase):
    """create_retriever 的后端选型：只有 PGVector（str collection）才走 Hybrid。"""

    def test_mock_store_without_collection_name_uses_threshold(self):
        store = mock.MagicMock(name="store")
        retriever = retrieval_service.create_retriever(store, k=4)
        self.assertIsInstance(retriever, ThresholdVectorStoreRetriever)

    def test_pg_store_with_hybrid_enabled_uses_hybrid(self):
        store = mock.MagicMock(name="store")
        store.collection_name = "user_1_kb"

        retriever = retrieval_service.create_retriever(store, k=4, use_hybrid=True)

        self.assertIsInstance(retriever, HybridVectorStoreRetriever)

    def test_hybrid_can_be_disabled_for_ablation(self):
        store = mock.MagicMock(name="store")
        store.collection_name = "user_1_kb"

        retriever = retrieval_service.create_retriever(store, k=4, use_hybrid=False)

        self.assertIsInstance(retriever, ThresholdVectorStoreRetriever)


if __name__ == "__main__":
    unittest.main()
