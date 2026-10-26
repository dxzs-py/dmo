"""知识库检索工具（C2）单测：唯一工具名、kb_ids 子集、sources、空结果。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_kb_search_tool --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.kb_search_tool import (
    KbToolBundle,
    build_knowledge_base_search_tool,
)
from Django_xm.apps.knowledge.services.rag_service import RagResult, RagScope

_PATCH = "Django_xm.apps.knowledge.services.rag_service.RagService.retrieve_scopes"


def _bundle(kb_id: str) -> KbToolBundle:
    return KbToolBundle(
        kb_id=kb_id,
        name=f"知识库-{kb_id}",
        description="",
        scope=RagScope(kind="persistent_kb", name=f"user_1_{kb_id}"),
        retriever=mock.MagicMock(name=f"retriever-{kb_id}"),
        vector_store=None,
    )


class KnowledgeBaseSearchToolTests(unittest.TestCase):
    def _tool(self):
        return build_knowledge_base_search_tool(
            bundles={"kb1": _bundle("kb1"), "kb2": _bundle("kb2")}, k=4
        )

    def test_name_is_unique(self):
        self.assertEqual(self._tool().name, "knowledge_base_search")

    def test_kb_ids_subset_only(self):
        tool = self._tool()
        captured = {}

        def _fake(query, *, bundles, k, apply_budget=True, metrics_kind=None, intent="auto"):
            captured["bundles"] = bundles
            captured["k"] = k
            captured["intent"] = intent
            return RagResult(
                docs=[Document(page_content="内容", metadata={"source": "a.md"})],
                sources=[{"source": "a.md", "scope": "persistent_kb"}],
                metrics={"hits": 1},
            )

        with mock.patch(_PATCH, side_effect=_fake):
            text, artifact = tool._run("查询", ["kb2"])

        self.assertEqual(len(captured["bundles"]), 1)
        self.assertEqual(captured["bundles"][0][0].name, "user_1_kb2")
        self.assertEqual(captured["k"], 4)
        self.assertEqual(captured["intent"], "auto")
        self.assertIn("内容", text)
        self.assertEqual(artifact["sources"][0]["source"], "a.md")

    def test_explicit_intent_forwarded(self):
        """显式 intent（全局分析）必须透传给 RagService（不能静默丢弃）。"""
        tool = self._tool()
        captured = {}

        def _fake(query, *, bundles, k, apply_budget=True, metrics_kind=None, intent="auto"):
            captured["intent"] = intent
            return RagResult(docs=[], sources=[], metrics={"hits": 0, "intent": intent})

        with mock.patch(_PATCH, side_effect=_fake):
            tool._run("这个知识库主要讲了什么", intent="comprehensive")

        self.assertEqual(captured["intent"], "comprehensive")

    def test_schema_exposes_intent(self):
        """输入 schema 暴露 intent（LLM 能自主选择 precise/comprehensive）。"""
        fields = self._tool().args_schema.model_fields
        self.assertIn("intent", fields)
        self.assertEqual(fields["intent"].default, "auto")

    def test_foreign_kb_rejected(self):
        text, artifact = self._tool()._run("查询", ["other"])

        self.assertIn("未找到可检索的知识库", text)
        self.assertEqual(artifact["sources"], [])

    def test_empty_result_controlled_text(self):
        with mock.patch(
            _PATCH,
            return_value=RagResult(docs=[], sources=[], metrics={"hits": 0}),
        ):
            text, artifact = self._tool()._run("查询")

        self.assertIn("未找到", text)
        self.assertEqual(artifact["sources"], [])

    def test_description_lists_kbs(self):
        description = self._tool().description
        self.assertIn("kb1", description)
        self.assertIn("知识库-kb2", description)


if __name__ == "__main__":
    unittest.main()
