"""Phase 2 质量增强回归：多路查询 / 多路融合 / token 预算 / 去冗余 / 邻块扩展。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_quality_phase2 --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.context_manager.services.compression import TokenEstimator
from Django_xm.apps.knowledge.config import settings
from Django_xm.apps.knowledge.services import rag_retrieval
from Django_xm.apps.knowledge.services.context_expansion import expand_adjacent
from Django_xm.apps.knowledge.services.context_format import (
    TRUNCATED_TAG,
    dedupe_documents,
    fit_documents_by_tokens,
    truncate_content_by_tokens,
)
from Django_xm.apps.knowledge.services.query_expansion import (
    build_retrieval_queries_async,
    build_retrieval_queries_sync,
)
from Django_xm.apps.knowledge.services.rag_retrieval import retrieve_multi_query

_QE = "Django_xm.apps.knowledge.services.query_expansion"


class QueryExpansionTests(unittest.TestCase):
    def test_without_llm_returns_original_only(self):
        self.assertEqual(build_retrieval_queries_sync("原问题", None), ["原问题"])

    def test_empty_query_returns_empty(self):
        self.assertEqual(build_retrieval_queries_sync("   ", mock.MagicMock()), [])

    def test_hyde_and_multi_query_merged(self):
        with (
            mock.patch(f"{_QE}._hyde_sync", return_value="假设文档"),
            mock.patch(f"{_QE}._multi_query_sync", return_value=["改写1", "改写2"]),
            mock.patch.object(settings, "rag_multi_query_enabled", True),
        ):
            queries = build_retrieval_queries_sync("原问题", mock.MagicMock())

        self.assertEqual(queries[0], "原问题")
        self.assertIn("假设文档", queries)
        self.assertEqual(queries[-2:], ["改写1", "改写2"])

    def test_multi_query_failure_keeps_hyde_route(self):
        with (
            mock.patch(f"{_QE}._hyde_sync", return_value="假设文档"),
            mock.patch(f"{_QE}._multi_query_sync", side_effect=RuntimeError("llm down")),
            mock.patch.object(settings, "rag_multi_query_enabled", True),
        ):
            queries = build_retrieval_queries_sync("原问题", mock.MagicMock())

        self.assertEqual(queries, ["原问题", "假设文档"])

    def test_duplicate_routes_deduped(self):
        with (
            mock.patch(f"{_QE}._hyde_sync", return_value="原问题"),
            mock.patch(f"{_QE}._multi_query_sync", return_value=["原问题"]),
            mock.patch.object(settings, "rag_multi_query_enabled", True),
        ):
            queries = build_retrieval_queries_sync("原问题", mock.MagicMock())

        self.assertEqual(queries, ["原问题"])

    def test_multi_query_can_be_disabled(self):
        with (
            mock.patch(f"{_QE}._hyde_sync", return_value="假设文档"),
            mock.patch(f"{_QE}._multi_query_sync") as mq,
            mock.patch.object(settings, "rag_multi_query_enabled", False),
        ):
            queries = build_retrieval_queries_sync("原问题", mock.MagicMock())

        mq.assert_not_called()
        self.assertEqual(queries, ["原问题", "假设文档"])


class QueryExpansionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_merges_routes(self):
        with (
            mock.patch(f"{_QE}._hyde_async", new=mock.AsyncMock(return_value="假设文档")),
            mock.patch(f"{_QE}._multi_query_async", new=mock.AsyncMock(return_value=["改写1"])),
            mock.patch.object(settings, "rag_multi_query_enabled", True),
        ):
            queries = await build_retrieval_queries_async("原问题", mock.MagicMock())

        self.assertEqual(queries, ["原问题", "假设文档", "改写1"])

    async def test_async_failure_keeps_original(self):
        with (
            mock.patch(f"{_QE}._hyde_async", new=mock.AsyncMock(side_effect=RuntimeError("x"))),
            mock.patch(f"{_QE}._multi_query_async", new=mock.AsyncMock(side_effect=RuntimeError("y"))),
            mock.patch.object(settings, "rag_multi_query_enabled", True),
        ):
            queries = await build_retrieval_queries_async("原问题", mock.MagicMock())

        self.assertEqual(queries, ["原问题"])


class RetrieveMultiQueryTests(unittest.TestCase):
    def _patch_pipeline(self, docs_by_query, side_effect=None):
        pipeline = mock.MagicMock(name="pipeline")
        if side_effect is not None:
            pipeline.retrieve_documents_from_retriever.side_effect = side_effect
        else:
            pipeline.retrieve_documents_from_retriever.side_effect = (
                lambda **kwargs: list(docs_by_query.get(kwargs["query"], []))
            )
        pipeline_cls = mock.MagicMock(return_value=pipeline)
        patcher = mock.patch.object(rag_retrieval, "UnifiedRagPipeline", pipeline_cls)
        patcher.start()
        self.addCleanup(patcher.stop)
        return pipeline

    def test_single_query_returns_directly(self):
        doc = Document(page_content="A", metadata={"source": "a.md"})
        pipeline = self._patch_pipeline({"q": [doc]})

        result = retrieve_multi_query("<r>", ["q"], vector_store=None, k=4)

        self.assertEqual(result, [doc])
        pipeline.retrieve_documents_from_retriever.assert_called_once()

    def test_multi_query_rrf_prefers_doc_hit_by_both_routes(self):
        first = Document(page_content="A", metadata={"source": "a.md", "chunk_index": 0})
        both = Document(page_content="B", metadata={"source": "b.md", "chunk_index": 0})
        self._patch_pipeline({"q1": [first, both], "q2": [both]})

        result = retrieve_multi_query("<r>", ["q1", "q2"], vector_store=None, k=2)

        self.assertEqual(result[0].page_content, "B")
        self.assertEqual({doc.page_content for doc in result}, {"A", "B"})

    def test_non_embedding_error_propagates(self):
        self._patch_pipeline({}, side_effect=ValueError("bad"))

        with self.assertRaises(ValueError):
            retrieve_multi_query("<r>", ["q"], vector_store=None, k=4)

    def test_empty_result_returns_empty(self):
        self._patch_pipeline({"q1": [], "q2": []})

        self.assertEqual(retrieve_multi_query("<r>", ["q1", "q2"], vector_store=None, k=4), [])


class TokenBudgetTests(unittest.TestCase):
    def test_truncate_by_tokens_marks_content(self):
        content, truncated = truncate_content_by_tokens("中文内容" * 500, 100)

        self.assertTrue(truncated)
        self.assertIn(TRUNCATED_TAG, content)
        self.assertLessEqual(TokenEstimator.estimate_tokens(content), 130)

    def test_short_content_untouched(self):
        content, truncated = truncate_content_by_tokens("短内容", 100)
        self.assertEqual((content, truncated), ("短内容", False))

    def test_fit_documents_respects_total_budget(self):
        docs = [Document(page_content="字" * 500, metadata={"source": f"{i}.md"}) for i in range(20)]

        fitted, exceeded = fit_documents_by_tokens(
            docs, max_doc_tokens=800, max_total_tokens=600
        )

        self.assertTrue(exceeded)
        total = sum(TokenEstimator.estimate_tokens(doc.page_content) for doc in fitted)
        self.assertLessEqual(total, 600)


class DedupeTests(unittest.TestCase):
    def test_identical_content_dropped(self):
        doc = "相同内容" * 100
        docs = [
            Document(page_content=doc, metadata={"source": "a.md"}),
            Document(page_content=doc, metadata={"source": "a.md"}),
        ]

        self.assertEqual(len(dedupe_documents(docs, similarity_threshold=0.85)), 1)

    def test_max_per_source(self):
        docs = [
            Document(page_content=f"不同内容{i}" * 50, metadata={"source": "a.md"})
            for i in range(5)
        ]

        kept = dedupe_documents(docs, max_per_source=2, similarity_threshold=1.0)

        self.assertEqual(len(kept), 2)

    def test_different_sources_kept(self):
        docs = [
            Document(page_content="相同内容" * 50, metadata={"source": "a.md"}),
            Document(page_content="相同内容" * 50, metadata={"source": "b.md"}),
        ]

        self.assertEqual(len(dedupe_documents(docs)), 2)


class ContextExpansionMemoryTests(unittest.TestCase):
    def _chunks(self, count=5, source="a.md"):
        return [
            Document(page_content=f"c{i}", metadata={"source": source, "chunk_index": i})
            for i in range(count)
        ]

    def test_expands_adjacent_in_order(self):
        chunks = self._chunks()

        expanded = expand_adjacent([chunks[2]], documents=chunks, window=1)

        self.assertEqual(
            [doc.metadata["chunk_index"] for doc in expanded], [1, 2, 3]
        )

    def test_window_zero_returns_original(self):
        chunks = self._chunks()
        self.assertEqual(expand_adjacent([chunks[2]], documents=chunks, window=0), [chunks[2]])

    def test_missing_chunk_index_not_expanded(self):
        doc = Document(page_content="x", metadata={"source": "a.md"})
        self.assertEqual(expand_adjacent([doc], documents=[doc], window=1), [doc])

    def test_no_source_returns_original(self):
        chunks = self._chunks()
        self.assertEqual(expand_adjacent(chunks, window=1), chunks)


if __name__ == "__main__":
    unittest.main()
