"""retrieval_node 表征测试（ARCH-08 / §5.1 B⑨）。

在检索入口收敛批次锁定 learning/nodes/retrieval_node 的现有外部行为：
节点检索核心已委托 UnifiedRagPipeline（retrieve_documents_from_retriever
+ 空结果时 load_kb_documents 全库降级），多知识库编排 / 全文精确去重 /
relevance_score 递减为本节点特有分层（分层非重复），收敛不得改变：

- _load_and_retrieve：
  * 正常：经 UnifiedRagPipeline.retrieve_documents_from_retriever 检索
    （vector_store=None，PGVector 不支持内存关键词检索），返回 (docs, False)
  * 索引不存在 / 加载异常 → ([], False)
  * 管线空结果 → load_kb_documents 全库加载 → pipeline.retrieve_documents
    重检索 → 命中返回 (docs, True)（降级标记）
  * 全库为空 → ([], False)
- retrieval_node：
  * 多知识库合并 + 按 page_content 全文精确去重（与管线 80 字符前缀去重口径不同）
  * relevance_score 按 1.0 - 0.1*i 递减
  * 未选知识库 → 空 retrieved_docs + 提示消息
  * 文档 < 3 时按 key_points 补充检索（每知识库每关键点 k=2）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_retrieval_node --noinput
（纯单元测试，无 DB / Redis / LLM 依赖；manage.py test 需 DB_NAME 指向独立测试库）
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.learning.nodes.retrieval_node import _load_and_retrieve, retrieval_node

_RN = "Django_xm.apps.learning.nodes.retrieval_node"
_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"


def _doc(content: str, source: str) -> Document:
    return Document(page_content=content, metadata={"source": source})


def _make_state(**overrides) -> dict:
    state = {
        "user_id": 1,
        "learning_plan": {"topic": "RAG 检索", "key_points": ["向量检索", "重排序"]},
        "knowledge_base_ids": ["kb1"],
        "use_web_search": False,
    }
    state.update(overrides)
    return state


def _make_pipeline_cls(retrieve_docs=None, retrieve_documents_return=None):
    """构造 UnifiedRagPipeline 类替身（延迟导入在调用时解析，patch 源模块属性）。"""
    pipeline_cls = mock.MagicMock()
    instance = pipeline_cls.return_value
    instance.retrieve_documents_from_retriever.return_value = list(retrieve_docs or [])
    instance.retrieve_documents.return_value = list(retrieve_documents_return or [])
    return pipeline_cls, instance


class LoadAndRetrieveTests(unittest.TestCase):
    """_load_and_retrieve：管线委托与全库降级路径。"""

    def _run(self, *, retrieve_docs=None, load_index_exc=None, kb_docs=None, degraded_docs=None):
        pipeline_cls, instance = _make_pipeline_cls(
            retrieve_docs=retrieve_docs, retrieve_documents_return=degraded_docs
        )
        manager = mock.MagicMock()
        if load_index_exc is not None:
            manager.load_index.side_effect = load_index_exc
        else:
            manager.load_index.return_value = mock.MagicMock(name="vector_store")

        with (
            mock.patch(f"{_RN}.get_index_manager", return_value=manager),
            mock.patch(f"{_RN}.get_embeddings", return_value=mock.MagicMock()),
            mock.patch(f"{_RN}.create_retriever", return_value=mock.MagicMock(name="retriever")),
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls),
            mock.patch(f"{_RAG_RETRIEVAL}.load_kb_documents", return_value=(list(kb_docs or []), 12)),
        ):
            docs, degraded = _load_and_retrieve("user_1_kb1", "RAG 检索", k=5)
        return docs, degraded, instance, manager

    def test_normal_path_delegates_to_pipeline(self):
        docs = [_doc("片段一", "a.md")]
        result, degraded, instance, _ = self._run(retrieve_docs=docs)

        self.assertEqual(result, docs)
        self.assertFalse(degraded)
        instance.retrieve_documents_from_retriever.assert_called_once()
        kwargs = instance.retrieve_documents_from_retriever.call_args.kwargs
        self.assertEqual(kwargs["query"], "RAG 检索")
        self.assertIsNone(kwargs["vector_store"])

    def test_index_missing_returns_empty(self):
        result, degraded, instance, _ = self._run(load_index_exc=FileNotFoundError("索引不存在"))

        self.assertEqual(result, [])
        self.assertFalse(degraded)
        instance.retrieve_documents_from_retriever.assert_not_called()

    def test_index_load_failure_returns_empty(self):
        result, degraded, instance, _ = self._run(load_index_exc=RuntimeError("加载失败"))

        self.assertEqual(result, [])
        self.assertFalse(degraded)
        instance.retrieve_documents_from_retriever.assert_not_called()

    def test_empty_pipeline_result_falls_back_to_full_kb(self):
        """管线空结果：load_kb_documents 全库加载后经 retrieve_documents 重检索，标记降级。"""
        kb_docs = [_doc("全库片段", "f.md")]
        degraded_docs = [_doc("降级命中", "f.md")]
        result, degraded, instance, _ = self._run(
            retrieve_docs=[], kb_docs=kb_docs, degraded_docs=degraded_docs
        )

        self.assertEqual(result, degraded_docs)
        self.assertTrue(degraded)
        instance.retrieve_documents.assert_called_once_with("RAG 检索", kb_docs, 12)

    def test_fallback_with_empty_kb_returns_empty(self):
        result, degraded, _, _ = self._run(retrieve_docs=[], kb_docs=[])

        self.assertEqual(result, [])
        self.assertFalse(degraded)


class RetrievalNodeTests(unittest.TestCase):
    """retrieval_node：多库合并 / 全文去重 / relevance_score / 未选库分支。"""

    def test_merges_kbs_and_dedups_by_full_content(self):
        doc_a = _doc("共同内容", "a.md")
        doc_b = _doc("片段B", "b.md")
        doc_a_dup = _doc("共同内容", "a.md")
        doc_c = _doc("片段C", "c.md")

        def fake_load(user_index_name, query, k=5):
            return ([doc_a, doc_b] if "kb1" in user_index_name else [doc_a_dup, doc_c]), False

        with (
            mock.patch(f"{_RN}._load_and_retrieve", side_effect=fake_load),
            mock.patch(f"{_RN}._run_web_search", return_value=[]),
        ):
            result = retrieval_node(_make_state(knowledge_base_ids=["kb1", "kb2"]))

        contents = [doc["content"] for doc in result["retrieved_docs"]]
        # 全文精确去重：跨库重复的"共同内容"只保留一次
        self.assertEqual(contents, ["共同内容", "片段B", "片段C"])
        scores = [doc["relevance_score"] for doc in result["retrieved_docs"]]
        self.assertEqual(scores, [1.0, 0.9, 0.8])
        self.assertEqual(result["current_step"], "retrieval")
        self.assertIn("2 个知识库", result["messages"][0]["content"])

    def test_no_kb_selected_skips_rag(self):
        result = retrieval_node(_make_state(knowledge_base_ids=[], user_id=1))

        self.assertEqual(result["retrieved_docs"], [])
        self.assertEqual(result["current_step"], "retrieval")
        self.assertIn("未选择知识库", result["messages"][0]["content"])

    def test_no_plan_skips_retrieval(self):
        result = retrieval_node(_make_state(learning_plan=None))

        self.assertEqual(result["retrieved_docs"], [])
        self.assertIn("学习计划生成失败", result["messages"][0]["content"])

    def test_supplementary_retrieval_by_key_points_when_docs_sparse(self):
        """主查询仅命中 1 篇（<3）时按 key_points 逐库补充检索（k=2）。"""
        doc_main = _doc("主查询命中", "a.md")
        doc_supp = _doc("关键点补充", "a.md")

        def fake_load(user_index_name, query, k=5):
            if query == "RAG 检索":
                return [doc_main], False
            if query == "向量检索" and k == 2:
                return [doc_supp], False
            return [], False

        with (
            mock.patch(f"{_RN}._load_and_retrieve", side_effect=fake_load),
            mock.patch(f"{_RN}._run_web_search", return_value=[]),
        ):
            result = retrieval_node(_make_state(knowledge_base_ids=["kb1"]))

        contents = [doc["content"] for doc in result["retrieved_docs"]]
        self.assertEqual(contents, ["主查询命中", "关键点补充"])

    def test_web_search_results_merged_first(self):
        doc_a = _doc("知识库片段", "a.md")

        with (
            mock.patch(f"{_RN}._load_and_retrieve", return_value=([doc_a], False)),
            mock.patch(f"{_RN}._run_web_search", return_value=["联网结果"]),
        ):
            result = retrieval_node(_make_state(use_web_search=True))

        contents = [doc["content"] for doc in result["retrieved_docs"]]
        self.assertEqual(contents, ["联网结果", "知识库片段"])
        self.assertEqual(result["retrieved_docs"][0]["metadata"]["source"], "web_search")


if __name__ == "__main__":
    unittest.main()
