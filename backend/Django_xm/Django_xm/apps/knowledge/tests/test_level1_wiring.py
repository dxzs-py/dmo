"""P0：Level 1 降级接线 + pgvector distance 语义回归。

改造前缺口：
- ``strict_rag_chain._retrieve_with_fallback`` 向
  ``retrieve_documents_from_retriever`` 传 ``vector_store=None``，Level 1
  （降阈扩召回 + 精排）在生产 KB 链路永不执行，检索未命中直接回「未找到」。
- ``_ScoreThresholdRetriever`` 硬编码「score 越大越相关」，而 pgvector 原生
  返回 cosine distance（越小越相关）；接线到 PG 后不归一会反向过滤，
  淘汰最相关文档。

本测试锁定：
1. 同步/异步初始检索均把 vector_store 与 k 透传给 pipeline；
2. Level 1 精排保留数不超过调用方 k 与 rag_rerank_top_n；
3. distance 语义归一后阈值过滤方向正确；
4. FlashRank 不可用时回退「去重后取前 N 条」。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_level1_wiring --noinput
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.config import settings
from Django_xm.apps.knowledge.services import rag_retrieval, retrieval_service, strict_rag_chain
from Django_xm.apps.knowledge.services.rag_retrieval import (
    UnifiedRagPipeline,
    _ScoreThresholdRetriever,
)

_RETRIEVAL_SERVICE = "Django_xm.apps.knowledge.services.retrieval_service"
_RERANK_MODEL = settings.rag_rerank_model


def _docs(count: int) -> list[Document]:
    return [Document(page_content=f"片段-{i}", metadata={"source": f"f{i}.md"}) for i in range(count)]


class Level1WiringTests(unittest.TestCase):
    """strict RAG 初始检索把 vector_store 透传到多路融合层。"""

    def _patch_retrieval(self):
        docs = _docs(1)
        retrieve = mock.patch.object(
            rag_retrieval, "retrieve_multi_query", return_value=docs
        )
        retrieve_mock = retrieve.start()
        self.addCleanup(retrieve.stop)
        return retrieve_mock

    def test_sync_passes_vector_store_and_k(self):
        retrieve = self._patch_retrieval()
        store = object()

        docs, degraded = strict_rag_chain._retrieve_with_fallback(
            "<retriever>", ["改写查询"], "原始查询", 4, "user_1_kb", store
        )

        retrieve.assert_called_once_with(
            "<retriever>", ["改写查询"], vector_store=store, k=4
        )
        self.assertEqual(len(docs), 1)
        self.assertFalse(degraded)

    def test_sync_without_vector_store_keeps_old_behavior(self):
        """未传 store（旧调用方）→ 仍传 None，Level 1 不可达（向后兼容）。"""
        retrieve = self._patch_retrieval()

        strict_rag_chain._retrieve_with_fallback("<r>", ["q1"], "q0", 4, "kb")

        self.assertIsNone(retrieve.call_args.kwargs["vector_store"])

    def test_async_passes_vector_store_and_k(self):
        retrieve = self._patch_retrieval()
        store = object()

        docs, degraded = asyncio.run(
            strict_rag_chain._aretrieve_with_fallback(
                "<retriever>", ["改写查询"], "原始查询", 4, "user_1_kb", store
            )
        )

        retrieve.assert_called_once_with(
            "<retriever>", ["改写查询"], vector_store=store, k=4
        )
        self.assertEqual(len(docs), 1)
        self.assertFalse(degraded)


class ScoreThresholdSemanticsTests(unittest.TestCase):
    """阈值适配器按后端语义归一后再过滤（pgvector distance 修复）。"""

    @staticmethod
    def _store(pairs):
        store = mock.MagicMock(name="vector_store")
        store.similarity_search_with_score.return_value = pairs
        return store

    def test_distance_semantics_keeps_most_relevant(self):
        """distance=0.2（相似度 0.8）保留；distance=0.9（相似度 0.1）被过滤。"""
        low = Document(page_content="低相关", metadata={})
        high = Document(page_content="高相关", metadata={})
        retriever = _ScoreThresholdRetriever(
            self._store([(low, 0.9), (high, 0.2)]),
            k=10,
            score_threshold=0.4,
            score_semantics="distance",
        )

        result = retriever.invoke("查询")

        self.assertEqual([doc.page_content for doc in result], ["高相关"])

    def test_similarity_semantics_keeps_most_relevant(self):
        low = Document(page_content="低相关", metadata={})
        high = Document(page_content="高相关", metadata={})
        retriever = _ScoreThresholdRetriever(
            self._store([(high, 0.9), (low, 0.2)]),
            k=10,
            score_threshold=0.4,
            score_semantics="similarity",
        )

        result = retriever.invoke("查询")

        self.assertEqual([doc.page_content for doc in result], ["高相关"])


class Level1TopNTests(unittest.TestCase):
    """Level 1 精排保留数透传与降级回退。"""

    def _patch_rerank(self, compression_docs: list[Document]):
        factory = mock.MagicMock(
            name="get_flashrank_reranker", return_value=mock.MagicMock(name="reranker")
        )
        compression = mock.MagicMock(name="compression_retriever")
        compression.invoke.return_value = compression_docs
        patchers = [
            mock.patch.object(retrieval_service, "FLASHRANK_AVAILABLE", True),
            mock.patch.object(retrieval_service, "ADVANCED_RETRIEVERS_AVAILABLE", True),
            mock.patch(f"{_RETRIEVAL_SERVICE}.get_flashrank_reranker", factory),
            # rerank_service 函数内导入该类，patch 源模块即可拦截真实 ONNX 加载
            mock.patch(
                "langchain_classic.retrievers.ContextualCompressionRetriever",
                mock.MagicMock(return_value=compression),
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        return factory, compression

    def _store(self, count: int):
        store = mock.MagicMock(name="vector_store")
        store.similarity_search_with_score.return_value = [
            (doc, 0.8) for doc in _docs(count)
        ]
        return store

    def test_top_n_from_caller_k(self):
        """调用方 k=4 → 精排保留 4（不超过 rag_rerank_top_n）。"""
        factory, _ = self._patch_rerank(_docs(2))

        UnifiedRagPipeline(scenario="knowledge_base")._degradation_level1_from_retriever(
            self._store(5), "查询", top_n=4
        )

        factory.assert_called_once_with(_RERANK_MODEL, 4)

    def test_top_n_never_exceeds_settings(self):
        """top_n=99 → 收敛到 rag_rerank_top_n，防止绕过上下文预算。"""
        factory, _ = self._patch_rerank(_docs(2))

        UnifiedRagPipeline(scenario="knowledge_base")._degradation_level1_from_retriever(
            self._store(5), "查询", top_n=99
        )

        factory.assert_called_once_with(_RERANK_MODEL, rag_retrieval.settings.rag_rerank_top_n)

    def test_falls_back_to_prefix_without_flashrank(self):
        """精排不可用/失败：rerank_service 回退为召回顺序前 N 条（不抛异常）。"""
        store = self._store(5)
        with mock.patch(
            "Django_xm.apps.knowledge.services.rerank_service.rerank_documents",
            side_effect=lambda docs, query, top_n, **kwargs: list(docs)[:top_n],
        ):
            docs = UnifiedRagPipeline(
                scenario="knowledge_base"
            )._degradation_level1_from_retriever(store, "查询", top_n=3)

        self.assertEqual(len(docs), 3)
        self.assertEqual([d.page_content for d in docs], ["片段-0", "片段-1", "片段-2"])

    def test_expanded_window_not_smaller_than_recall(self):
        """Level 1 扩展窗口取 max(rag_degraded_k, rag_recall_k)，不得小于正常路径。"""
        captured = {}
        real_factory = rag_retrieval._ScoreThresholdRetriever

        def _capture(store, k, score_threshold, score_semantics="similarity"):
            captured["k"] = k
            return real_factory(
                store, k=k, score_threshold=score_threshold, score_semantics=score_semantics
            )

        with (
            mock.patch.object(rag_retrieval, "_ScoreThresholdRetriever", side_effect=_capture),
            mock.patch(
                "Django_xm.apps.knowledge.services.rerank_service.rerank_documents",
                return_value=[],
            ),
        ):
            UnifiedRagPipeline(scenario="knowledge_base")._degradation_level1_from_retriever(
                self._store(5), "查询", top_n=3
            )

        self.assertEqual(
            captured["k"],
            max(rag_retrieval.settings.rag_degraded_k, rag_retrieval.settings.rag_recall_k),
        )


if __name__ == "__main__":
    unittest.main()
