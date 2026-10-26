"""RagService / ContextPipeline 单测（C1）。

覆盖：
- build_context：去重 → 邻块 → 预算（可选）→ sources 编号稳定；
- RagService.retrieve：scope 标记、sources、metrics、budget_exceeded、异常上抛。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_service --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services import rag_context_pipeline as pipeline
from Django_xm.apps.knowledge.services import rag_service
from Django_xm.apps.knowledge.services.rag_service import (
    RagRequest,
    RagScope,
    RagService,
)

_RUNTIME = {
    "dedupe_mode": "gram",
    "dedupe_similarity": 0.85,
    "max_chunks_per_source": 2,
    "context_window": 0,
    "doc_max_tokens": 800,
    "context_max_tokens": 3000,
}


def _doc(content, source, chunk_index=0, score=None, degraded=False):
    metadata = {"source": source, "chunk_index": chunk_index, "degraded": degraded}
    if score is not None:
        metadata["score"] = score
    return Document(page_content=content, metadata=metadata)


class ContextPipelineTests(unittest.TestCase):
    def test_group_by_source_first_seen_and_chunk_order(self):
        docs = [
            _doc("a2", "a.md", 2),
            _doc("b1", "b.md", 1),
            _doc("a1", "a.md", 1),
        ]

        groups = pipeline.group_by_source(docs)

        self.assertEqual([[d.page_content for d in g] for g in groups], [["a1", "a2"], ["b1"]])

    def test_build_sources_stable_numbering_and_scope(self):
        docs = [
            _doc("a1", "a.md", 1, score=0.9),
            _doc("a2", "a.md", 2),
            _doc("b1", "b.md", 1),
        ]
        for doc in docs:
            doc.metadata["_rag_scope"] = "session_attachment"

        sources = pipeline.build_sources(docs)

        self.assertEqual([s["citation"] for s in sources], [1, 2])
        self.assertEqual([s["source"] for s in sources], ["a.md", "b.md"])
        self.assertEqual(sources[0]["chunk_indexes"], [1, 2])
        self.assertEqual(sources[0]["scope"], "session_attachment")
        self.assertEqual(sources[0]["score"], 0.9)

    def test_build_context_applies_dedupe_expand_and_budget(self):
        docs = [_doc("a", "a.md")]
        with (
            mock.patch.object(pipeline, "get_rag_runtime_settings", return_value=_RUNTIME),
            mock.patch.object(pipeline, "dedupe_documents", side_effect=lambda d, **kw: d) as dedupe,
            mock.patch.object(pipeline, "expand_adjacent", side_effect=lambda d, **kw: d) as expand,
            mock.patch.object(
                pipeline, "fit_documents_by_tokens", return_value=([_doc("a", "a.md")], True)
            ) as fit,
        ):
            result = pipeline.build_context(docs, collection_name="kb", apply_budget=True)

        dedupe.assert_called_once()
        expand.assert_called_once()
        fit.assert_called_once()
        self.assertTrue(result.budget_exceeded)
        self.assertEqual(len(result.sources), 1)

    def test_build_context_skips_budget_when_disabled(self):
        docs = [_doc("a", "a.md")]
        with (
            mock.patch.object(pipeline, "get_rag_runtime_settings", return_value=_RUNTIME),
            mock.patch.object(pipeline, "dedupe_documents", side_effect=lambda d, **kw: d),
            mock.patch.object(pipeline, "expand_adjacent", side_effect=lambda d, **kw: d),
            mock.patch.object(pipeline, "fit_documents_by_tokens") as fit,
        ):
            result = pipeline.build_context(docs, collection_name="kb", apply_budget=False)

        fit.assert_not_called()
        self.assertFalse(result.budget_exceeded)

    def test_build_context_empty(self):
        self.assertEqual(pipeline.build_context([]).docs, [])


class RagServiceTests(unittest.TestCase):
    def _call(self, docs, scope_kind="persistent_kb"):
        pipeline_cls = mock.MagicMock()
        pipeline_cls.return_value.retrieve_documents_from_retriever.return_value = docs
        request = RagRequest(
            query="q",
            scope=RagScope(kind=scope_kind, name="kb" if scope_kind == "persistent_kb" else "attach_t"),
            k=4,
        )
        with (
            mock.patch(
                "Django_xm.apps.knowledge.services.rag_retrieval.UnifiedRagPipeline",
                pipeline_cls,
            ),
            mock.patch.object(
                rag_service,
                "build_context",
                side_effect=lambda d, **kw: pipeline.build_context(
                    d,
                    runtime=_RUNTIME,
                    apply_budget=kw.get("apply_budget", True),
                    default_scope=kw.get("default_scope", "persistent_kb"),
                ),
            ),
        ):
            result = RagService().retrieve(
                request, retriever=mock.MagicMock(), vector_store=mock.MagicMock()
            )
        return result, pipeline_cls

    def test_retrieve_returns_sources_and_scope(self):
        result, pipeline_cls = self._call([_doc("a", "a.md", 1, score=0.8)], "session_attachment")

        self.assertEqual(len(result.docs), 1)
        self.assertEqual(result.sources[0]["source"], "a.md")
        self.assertEqual(result.sources[0]["scope"], "session_attachment")
        self.assertEqual(result.metrics["scope"], "session_attachment")
        pipeline_cls.return_value.retrieve_documents_from_retriever.assert_called_once()

    def test_retrieve_empty_docs(self):
        result, _ = self._call([])

        self.assertEqual(result.docs, [])
        self.assertEqual(result.sources, [])
        self.assertEqual(result.metrics["hits"], 0)

    def test_retrieve_propagates_errors(self):
        pipeline_cls = mock.MagicMock()
        pipeline_cls.return_value.retrieve_documents_from_retriever.side_effect = RuntimeError("pg down")

        with mock.patch(
            "Django_xm.apps.knowledge.services.rag_retrieval.UnifiedRagPipeline",
            pipeline_cls,
        ), self.assertRaises(RuntimeError):
            RagService().retrieve(
                RagRequest(query="q", scope=RagScope(kind="persistent_kb", name="kb")),
                retriever=mock.MagicMock(),
            )


if __name__ == "__main__":
    unittest.main()
