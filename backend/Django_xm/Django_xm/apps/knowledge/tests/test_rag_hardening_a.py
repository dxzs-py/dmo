"""A 收口批回归：共享零件 / 缓存指纹 / 工具 Level 1 / 精排复用件。

锁定：
1. ``context_format`` 是单文档截断与总量预算的单源，边界声明同时进入
   strict prompt 与工具文案；
2. 查询缓存模式段携带检索配置指纹（阈值/窗口/精排/hybrid 任一变化即失配）；
3. ``rerank_service.rerank_documents`` 读取可配置模型、失败回退、支持精排分数闸门。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_hardening_a --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge import views_rag
from Django_xm.apps.knowledge.config import settings
from Django_xm.apps.knowledge.services import rerank_service, retrieval_service
from Django_xm.apps.knowledge.services.context_format import (
    DATA_BOUNDARY_NOTICE,
    RAG_CONTEXT_MAX_TOTAL_CHARS,
    RAG_DOC_MAX_CHARS,
    TRUNCATED_TAG,
    fit_documents,
    truncate_content,
    truncate_document,
)
from Django_xm.apps.knowledge.services.kb_search_tool import KB_RESULT_INSTRUCTION
from Django_xm.apps.knowledge.services.strict_rag_chain import STRICT_RAG_QA_PROMPT

_RETRIEVAL_SERVICE = "Django_xm.apps.knowledge.services.retrieval_service"


class ContextFormatTests(unittest.TestCase):
    """O18：截断 / 总量预算 / 边界声明单源。"""

    def test_truncate_content_marks_and_strips(self):
        content, truncated = truncate_content("  " + "甲" * (RAG_DOC_MAX_CHARS + 10) + "  ")
        self.assertTrue(truncated)
        self.assertTrue(content.endswith(TRUNCATED_TAG))
        self.assertEqual(len(content), RAG_DOC_MAX_CHARS + len(TRUNCATED_TAG))

    def test_truncate_document_keeps_original_when_short(self):
        doc = Document(page_content="短内容", metadata={"source": "a.md"})
        self.assertIs(truncate_document(doc), doc)

    def test_fit_documents_respects_total_budget(self):
        docs = [
            Document(page_content="甲" * RAG_DOC_MAX_CHARS, metadata={}) for _ in range(20)
        ]
        fitted, exceeded = fit_documents(docs)
        self.assertTrue(exceeded)
        self.assertLessEqual(
            sum(len(d.page_content) for d in fitted), RAG_CONTEXT_MAX_TOTAL_CHARS
        )

    def test_boundary_notice_shared_by_prompt_and_tool_instruction(self):
        self.assertIn(DATA_BOUNDARY_NOTICE, STRICT_RAG_QA_PROMPT)
        self.assertIn(DATA_BOUNDARY_NOTICE, KB_RESULT_INSTRUCTION)


class CacheFingerprintTests(unittest.TestCase):
    """O1：检索配置变化必须让查询缓存键失配。"""

    def setUp(self):
        self._originals = {
            "rag_score_threshold": settings.rag_score_threshold,
            "rag_recall_k": settings.rag_recall_k,
            "retriever_use_reranker": settings.retriever_use_reranker,
        }
        for key, value in self._originals.items():
            self.addCleanup(setattr, settings, key, value)
        patcher = mock.patch.object(views_rag, "get_model_string", return_value="m-test")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _segments(self):
        return views_rag._query_cache_segments()[1]

    def test_fingerprint_contains_retrieval_config(self):
        settings.rag_score_threshold = 0.35
        settings.rag_recall_k = 30
        settings.retriever_use_reranker = True

        segment = self._segments()

        self.assertIn("t0.35", segment)
        self.assertIn("r30", segment)
        self.assertIn("e1", segment)

    def test_threshold_change_invalidates_key(self):
        settings.rag_score_threshold = 0.35
        first = self._segments()
        settings.rag_score_threshold = 0.7
        self.assertNotEqual(first, self._segments())

    def test_recall_window_change_invalidates_key(self):
        settings.rag_recall_k = 30
        first = self._segments()
        settings.rag_recall_k = 50
        self.assertNotEqual(first, self._segments())


class RerankServiceTests(unittest.TestCase):
    """O3：精排复用件（模型可配置 / 失败回退 / 分数闸门）。"""

    def _docs(self):
        return [
            Document(page_content="A", metadata={}),
            Document(page_content="B", metadata={}),
        ]

    def _patch_rerank(self, reranked):
        compression = mock.MagicMock(name="compression")
        compression.invoke.return_value = reranked
        factory = mock.MagicMock(name="factory", return_value=mock.MagicMock())
        patchers = [
            mock.patch.object(retrieval_service, "FLASHRANK_AVAILABLE", True),
            mock.patch.object(retrieval_service, "ADVANCED_RETRIEVERS_AVAILABLE", True),
            mock.patch(f"{_RETRIEVAL_SERVICE}.get_flashrank_reranker", factory),
            mock.patch(
                "langchain_classic.retrievers.ContextualCompressionRetriever",
                mock.MagicMock(return_value=compression),
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        return factory, compression

    def test_uses_configured_model_and_cached_factory(self):
        docs = self._docs()
        factory, _ = self._patch_rerank([docs[1], docs[0]])

        result = rerank_service.rerank_documents(docs, "查询", 2)

        factory.assert_called_once_with(settings.rag_rerank_model, 2)
        self.assertEqual([d.page_content for d in result], ["B", "A"])

    def test_falls_back_to_prefix_on_failure(self):
        docs = self._docs()
        with (
            mock.patch.object(retrieval_service, "FLASHRANK_AVAILABLE", True),
            mock.patch.object(retrieval_service, "ADVANCED_RETRIEVERS_AVAILABLE", True),
            mock.patch(
                f"{_RETRIEVAL_SERVICE}.get_flashrank_reranker",
                side_effect=RuntimeError("onnx load failed"),
            ),
        ):
            result = rerank_service.rerank_documents(docs, "查询", 1)

        self.assertEqual([d.page_content for d in result], ["A"])

    def test_score_threshold_filters_low_relevance(self):
        docs = self._docs()
        high = Document(page_content="高", metadata={"relevance_score": 0.8})
        low = Document(page_content="低", metadata={"relevance_score": 0.01})
        self._patch_rerank([high, low])

        result = rerank_service.rerank_documents(docs, "查询", 2, score_threshold=0.1)

        self.assertEqual([d.page_content for d in result], ["高"])

    def test_empty_candidates_returns_empty(self):
        self.assertEqual(rerank_service.rerank_documents([], "查询", 3), [])


if __name__ == "__main__":
    unittest.main()
