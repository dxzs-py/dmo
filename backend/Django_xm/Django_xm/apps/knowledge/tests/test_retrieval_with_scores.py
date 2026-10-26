"""R-UX-04：检索内核 with_scores 相关度分数透出测试。

缺陷（修复前）：strict_rag_chain 检索经 UnifiedRagPipeline.retrieve_documents_from_retriever
→ retriever.invoke，Document 不携带分数——sources payload 无相关度分数，
前端引用面板无法展示"越相关分数越高"的可信度信号（R-UX-04③）。

修复契约（本文件锁定）：
1. with_scores=True 且 retriever 为纯 similarity 的 VectorStoreRetriever
   （鸭子判定：持有 vector_store 且 search_type=="similarity"）时，改走
   UnifiedRagPipeline.search_scored 统一打分内核（检索次数不变，无二次检索），
   归一相似度分数写入 doc.metadata["score"]；
2. score_semantics 取 vector_store.native_score_semantics 自描述（C-2arch），
   pgvector distance 语义自动归一，调用方无需感知后端差异；
3. MMR / 无 vector_store / with_scores=False 的路径保持 retriever.invoke 原样
   （不带分数，学习工作流等既有调用方零行为变化）；
4. 异常语义不变：search_scored 异常同样由 raise_on_error 决定上抛或吞掉返回空。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_retrieval_with_scores --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

import unittest
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from Django_xm.apps.knowledge.config import settings as app_cfg
from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"


def _doc(content: str, metadata: dict | None = None):
    from langchain_core.documents import Document

    return Document(page_content=content, metadata=metadata or {})


def _scored_store(score_semantics="similarity"):
    """向量库桩：仅承载 native_score_semantics 声明（search_scored 本身被 patch）。"""
    return SimpleNamespace(native_score_semantics=score_semantics)


def _similarity_retriever(store, invoke_docs=None):
    """纯 similarity VectorStoreRetriever 形态桩（鸭子三要素齐备）。"""
    retriever = mock.MagicMock(name="retriever")
    retriever.vector_store = store
    retriever.search_type = "similarity"
    retriever.search_kwargs = {"k": 7}
    retriever.invoke.return_value = invoke_docs if invoke_docs is not None else []
    return retriever


class WithScoresPathTests(SimpleTestCase):
    def setUp(self):
        # 3.2：打分路径接入 rag_score_threshold——测试锁定已知阈值
        p = mock.patch.object(app_cfg, "rag_score_threshold", 0.7)
        p.start()
        self.addCleanup(p.stop)

    def test_similarity_retriever_gets_normalized_scores(self):
        """with_scores=True：分数写入 metadata["score"]，None 分数不带键。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = _similarity_retriever(_scored_store())
        scored = [(_doc("d1"), 0.873), (_doc("d2"), None)]

        with mock.patch.object(
            UnifiedRagPipeline, "search_scored", return_value=scored
        ) as search_scored:
            docs = pipeline.retrieve_documents_from_retriever(
                retriever=retriever, query="q", with_scores=True
            )

        self.assertEqual(docs[0].metadata["score"], 0.873)
        self.assertNotIn("score", docs[1].metadata)
        # 统一打分内核收到 store 自声明的语义；k 取自 search_kwargs；
        # 3.2：阈值来自 settings.rag_score_threshold（setUp 锁定为 0.7）
        search_scored.assert_called_once_with(
            retriever.vector_store, "q", k=7, score_threshold=0.7, score_semantics="similarity"
        )
        # 纯 similarity 带分路径检索次数等价：不回退 invoke（无二次检索）
        retriever.invoke.assert_not_called()

    def test_distance_semantics_forwarded(self):
        """pgvector 类后端（native_score_semantics="distance"）语义透传给归一内核。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = _similarity_retriever(_scored_store(score_semantics="distance"))

        with mock.patch.object(
            UnifiedRagPipeline, "search_scored", return_value=[(_doc("d"), 0.5)]
        ) as search_scored:
            docs = pipeline.retrieve_documents_from_retriever(
                retriever=retriever, query="q", with_scores=True
            )

        search_scored.assert_called_once_with(
            retriever.vector_store, "q", k=7, score_threshold=0.7, score_semantics="distance"
        )
        self.assertEqual(docs[0].metadata["score"], 0.5)

    def test_default_without_scores_keeps_invoke(self):
        """with_scores=False（默认）：invoke 原路径，学习工作流等调用方零行为变化。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = _similarity_retriever(_scored_store(), invoke_docs=[_doc("d", {"file_name": "a"})])

        with mock.patch.object(UnifiedRagPipeline, "search_scored") as search_scored:
            docs = pipeline.retrieve_documents_from_retriever(retriever=retriever, query="q")

        search_scored.assert_not_called()
        retriever.invoke.assert_called_once_with("q")
        self.assertEqual(docs, [_doc("d", {"file_name": "a"})])

    def test_mmr_retriever_keeps_invoke(self):
        """MMR 检索器（search_type="mmr"）：不满足纯 similarity 条件，保持 invoke 不带分。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = _similarity_retriever(_scored_store())
        retriever.search_type = "mmr"

        with mock.patch.object(UnifiedRagPipeline, "search_scored") as search_scored:
            pipeline.retrieve_documents_from_retriever(
                retriever=retriever, query="q", with_scores=True
            )

        search_scored.assert_not_called()
        retriever.invoke.assert_called_once_with("q")

    def test_non_vectorstore_retriever_keeps_invoke(self):
        """无 vector_store 属性的检索器（MultiQuery/Compression 包装）：保持 invoke。"""

        class _InvokeOnlyRetriever:
            search_type = "default"

            def __init__(self):
                self.calls = []

            def invoke(self, query):
                self.calls.append(query)
                return [_doc("d")]

        retriever = _InvokeOnlyRetriever()
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")

        with mock.patch.object(UnifiedRagPipeline, "search_scored") as search_scored:
            docs = pipeline.retrieve_documents_from_retriever(
                retriever=retriever, query="q", with_scores=True
            )

        search_scored.assert_not_called()
        self.assertEqual(docs, [_doc("d")])
        self.assertEqual(retriever.calls, ["q"])

    def test_raise_on_error_propagates_scored_failure(self):
        """raise_on_error=True 时 search_scored 异常同样上抛（C-3 降级链可达语义保持）。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = _similarity_retriever(_scored_store())

        with (
            mock.patch.object(
                UnifiedRagPipeline, "search_scored", side_effect=ConnectionError("db down")
            ),
            self.assertRaises(ConnectionError),
        ):
            pipeline.retrieve_documents_from_retriever(
                retriever=retriever, query="q", raise_on_error=True, with_scores=True
            )

    def test_scored_failure_swallowed_by_default(self):
        """默认吞异常返回空列表（既有契约保持）。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = _similarity_retriever(_scored_store())

        with mock.patch.object(
            UnifiedRagPipeline, "search_scored", side_effect=ConnectionError("db down")
        ):
            docs = pipeline.retrieve_documents_from_retriever(
                retriever=retriever, query="q", with_scores=True
            )

        self.assertEqual(docs, [])


class StrictChainWiringTests(SimpleTestCase):
    def test_strict_chain_passes_with_scores(self):
        """strict_rag_chain 两侧检索必须经多路融合入口（内部固定 with_scores=True）。"""
        from Django_xm.apps.knowledge.services import rag_retrieval as rr
        from Django_xm.apps.knowledge.services import rag_service as svc
        from Django_xm.apps.knowledge.services import strict_rag_chain as src

        with open(src.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        self.assertGreaterEqual(
            source.count("retrieve_many"),
            2,
            "sync/async 两侧检索均须经 RagService.retrieve_many",
        )

        with open(svc.__file__, encoding="utf-8") as service_file:
            self.assertIn("retrieve_multi_query", service_file.read(), "RagService 内部须走多路融合")

        with open(rr.__file__, encoding="utf-8") as retrieval_file:
            self.assertIn("with_scores=True", retrieval_file.read(), "多路融合内部必须透出分数")


if __name__ == "__main__":
    unittest.main()
