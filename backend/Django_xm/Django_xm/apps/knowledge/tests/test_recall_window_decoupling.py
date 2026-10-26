"""P0 检索改造：召回窗口与精排窗口解耦 + 阈值前置回归。

改造前缺口：
- ``create_retriever`` 以 ``k``（默认 4）作为向量召回数，而 FlashRank
  ``top_n``（默认 6）大于等于 k —— 精排只能在既有 4 条里换序，召回集合不变，
  PR 曲线无收益。
- similarity 阈值只存在于裸 ``VectorStoreRetriever`` 路径；一旦套上
  ``ContextualCompressionRetriever``（rerank），``_initial_search_from_retriever``
  的 with_scores 鸭子判定落空，``rag_score_threshold`` 静默失效。

本测试锁定改造后的行为：
1. use_reranker=True → 召回窗口 = max(recall_k or rag_recall_k, k)，
   精排保留数 = min(k, rag_rerank_top_n)；
2. use_reranker=False → 召回窗口 = k（行为不变）；
3. 阈值由 ThresholdVectorStoreRetriever 在精排之前应用，并按后端语义归一；
4. rerank 开启时不写精排前的 similarity 到 metadata["score"]；
5. 检索器不暴露 vector_store / search_type 属性（避免二次检索）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_recall_window_decoupling --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services import retrieval_service
from Django_xm.apps.knowledge.services.retrieval_service import ThresholdVectorStoreRetriever

_VECTOR_STORE = "Django_xm.apps.knowledge.vector_store"


def _fake_store(**attrs):
    """构造带可选属性（如 native_score_semantics）的向量库替身。"""
    store = mock.MagicMock(name="vector_store")
    for key, value in attrs.items():
        setattr(store, key, value)
    return store


class RecallWindowDecouplingTests(unittest.TestCase):
    """召回窗口与精排保留数解耦。"""

    def setUp(self):
        self._original_recall = retrieval_service.settings.rag_recall_k
        self.addCleanup(
            setattr, retrieval_service.settings, "rag_recall_k", self._original_recall
        )
        # 精排器与压缩包装器打桩：只观察窗口/top_n 传递，不加载 ONNX
        reranker_patcher = mock.patch.object(
            retrieval_service, "create_reranker", return_value=object()
        )
        self.create_reranker = reranker_patcher.start()
        self.addCleanup(reranker_patcher.stop)
        wrap_patcher = mock.patch.object(
            retrieval_service,
            "create_reranking_retriever",
            side_effect=lambda base, reranker: base,
        )
        wrap_patcher.start()
        self.addCleanup(wrap_patcher.stop)

    def test_rerank_recall_window_defaults_to_settings(self):
        """k=4 + reranker → 召回窗口取 rag_recall_k(30)，精排保留 4。"""
        retrieval_service.settings.rag_recall_k = 30

        retriever = retrieval_service.create_retriever(_fake_store(), k=4, use_reranker=True)

        self.assertIsInstance(retriever, ThresholdVectorStoreRetriever)
        self.assertEqual(retriever._k, 30)
        self.assertEqual(self.create_reranker.call_args.kwargs["top_n"], 4)

    def test_rerank_top_n_capped_by_settings(self):
        """k=10 超过 rag_rerank_top_n → 精排保留数为配置上限（6），召回收敛为 30。"""
        retrieval_service.settings.rag_recall_k = 30

        retriever = retrieval_service.create_retriever(_fake_store(), k=10, use_reranker=True)

        self.assertEqual(retriever._k, 30)
        self.assertEqual(
            self.create_reranker.call_args.kwargs["top_n"],
            retrieval_service.settings.rag_rerank_top_n,
        )

    def test_explicit_recall_k_wins(self):
        """显式 recall_k 覆盖配置默认值。"""
        retriever = retrieval_service.create_retriever(
            _fake_store(), k=4, use_reranker=True, recall_k=50
        )
        self.assertEqual(retriever._k, 50)

    def test_recall_window_never_below_final_k(self):
        """recall_k < k 时抬升到 k，避免召回窗口小于交付条数（负向剪枝）。"""
        retriever = retrieval_service.create_retriever(
            _fake_store(), k=10, use_reranker=True, recall_k=2
        )
        self.assertEqual(retriever._k, 10)

    def test_without_reranker_window_equals_k(self):
        """未启用 reranker → 召回窗口等于 k，且不构造重排器（行为不变）。"""
        retriever = retrieval_service.create_retriever(_fake_store(), k=4)

        self.assertEqual(retriever._k, 4)
        self.create_reranker.assert_not_called()

    def test_mmr_window_and_fetch_k_scaled(self):
        """MMR + reranker → k=召回窗口、fetch_k 不小于 2 倍窗口。"""
        store = _fake_store()
        store.as_retriever.return_value = mock.MagicMock(name="mmr_retriever")

        retrieval_service.create_retriever(
            store, k=4, search_type="mmr", use_reranker=True
        )

        kwargs = store.as_retriever.call_args.kwargs
        self.assertEqual(kwargs["search_type"], "mmr")
        self.assertEqual(kwargs["search_kwargs"]["k"], 30)
        self.assertGreaterEqual(kwargs["search_kwargs"]["fetch_k"], 60)


class ThresholdBeforeRerankTests(unittest.TestCase):
    """阈值是精排之外的固定环节：先过滤，再精排。"""

    def setUp(self):
        self._original_recall = retrieval_service.settings.rag_recall_k
        self.addCleanup(
            setattr, retrieval_service.settings, "rag_recall_k", self._original_recall
        )
        reranker_patcher = mock.patch.object(
            retrieval_service, "create_reranker", return_value=object()
        )
        reranker_patcher.start()
        self.addCleanup(reranker_patcher.stop)
        wrap_patcher = mock.patch.object(
            retrieval_service,
            "create_reranking_retriever",
            side_effect=lambda base, reranker: base,
        )
        wrap_patcher.start()
        self.addCleanup(wrap_patcher.stop)

    def test_threshold_and_semantics_passed_to_search_kernel(self):
        """检索器把 score_threshold / 后端语义传入统一检索内核。"""
        store = _fake_store(native_score_semantics="distance")
        retriever = retrieval_service.create_retriever(
            store, k=4, use_reranker=True, score_threshold=0.7
        )

        with mock.patch(f"{_VECTOR_STORE}.search_vector_store", return_value=[]) as search:
            docs = retriever.invoke("查询")

        self.assertEqual(docs, [])
        search.assert_called_once_with(
            store,
            "查询",
            k=30,
            score_threshold=0.7,
            score_semantics="distance",
            search_kwargs={},
        )

    def test_rerank_on_does_not_write_pre_rerank_score(self):
        """rerank 开启：精排前不写 metadata['score']（避免遮蔽 relevance_score）。"""
        doc = Document(page_content="候选", metadata={})
        store = _fake_store()
        retriever = retrieval_service.create_retriever(
            store, k=4, use_reranker=True, score_threshold=0.7
        )

        with mock.patch(f"{_VECTOR_STORE}.search_vector_store", return_value=[(doc, 0.9)]):
            docs = retriever.invoke("查询")

        self.assertEqual(len(docs), 1)
        self.assertNotIn("score", docs[0].metadata)

    def test_no_rerank_writes_normalized_score(self):
        """未启用 rerank：归一 similarity 写入 metadata['score'] 供 sources 透出。"""
        doc = Document(page_content="候选", metadata={})
        retriever = retrieval_service.create_retriever(
            _fake_store(), k=4, score_threshold=0.5
        )

        with mock.patch(f"{_VECTOR_STORE}.search_vector_store", return_value=[(doc, 0.82)]):
            docs = retriever.invoke("查询")

        self.assertEqual(docs[0].metadata["score"], 0.82)

    def test_retriever_does_not_expose_vector_store_attr(self):
        """不暴露 vector_store / search_type：避免 with_scores 鸭子判定二次检索。"""
        retriever = retrieval_service.create_retriever(_fake_store(), k=4)

        self.assertFalse(hasattr(retriever, "vector_store"))
        self.assertIsNone(getattr(retriever, "search_type", None))

    def test_invalid_backend_semantics_falls_back_to_similarity(self):
        """mock/第三方 store 的非法语义值回退 similarity，不抛 ValueError（R5-4）。"""
        store = mock.MagicMock(name="vector_store")
        store.native_score_semantics = mock.MagicMock()  # 非字符串

        retriever = retrieval_service.create_retriever(store, k=4)

        self.assertEqual(retriever._score_semantics, "similarity")


if __name__ == "__main__":
    unittest.main()
