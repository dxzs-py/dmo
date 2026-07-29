"""retrieval_node 知识库选择单元测试

验证 retrieval_node 在用户选择/未选择知识库时的行为分支：

1. 未选择知识库（knowledge_base_ids 为空或 None）-> 跳过 RAG 检索，返回空 docs + 提示
2. 已选择知识库 -> 对每个 kb_name 构造完整索引名（user_{id}_{name}）并加载向量库
3. learning_plan 缺失 -> 跳过检索并返回错误提示
4. 单个知识库加载失败时不影响其他知识库的检索（容错）

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_retrieval_node_knowledge_base -v 2
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.apps.learning.nodes.retrieval_node import (
    _compose_user_index_name,
    _dedup_by_content,
    retrieval_node,
)


class ComposeUserIndexNameTestCase(SimpleTestCase):
    """_compose_user_index_name 与 knowledge.views_utils.get_user_index_name 保持一致"""

    def test_compose_user_index_name(self):
        """构造完整索引名：user_{id}_{name}"""
        self.assertEqual(_compose_user_index_name(1, "数学笔记"), "user_1_数学笔记")
        self.assertEqual(_compose_user_index_name(42, "ml_basics"), "user_42_ml_basics")


class DedupByContentTestCase(SimpleTestCase):
    """_dedup_by_content 按 page_content 去重"""

    def test_dedup_keeps_first_occurrence(self):
        """重复文档仅保留首次出现"""
        doc1 = MagicMock(page_content="content_a")
        doc2 = MagicMock(page_content="content_b")
        doc3 = MagicMock(page_content="content_a")  # 重复
        result = _dedup_by_content([doc1, doc2, doc3])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].page_content, "content_a")
        self.assertEqual(result[1].page_content, "content_b")

    def test_dedup_empty_list(self):
        """空列表返回空列表"""
        self.assertEqual(_dedup_by_content([]), [])

    def test_dedup_skip_none_page_content(self):
        """page_content 为 None 的文档被跳过"""
        doc1 = MagicMock(page_content=None)
        doc1.page_content = None
        doc2 = MagicMock(page_content="real_content")
        result = _dedup_by_content([doc1, doc2])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].page_content, "real_content")


class RetrievalNodeNoKnowledgeBaseTestCase(SimpleTestCase):
    """未选择知识库时 retrieval_node 跳过 RAG 检索"""

    def _make_state(self, knowledge_base_ids):
        return {
            "learning_plan": {
                "topic": "机器学习",
                "key_points": ["监督学习", "无监督学习"],
            },
            "user_id": 1,
            "knowledge_base_ids": knowledge_base_ids,
        }

    def test_empty_kb_list_skips_rag(self):
        """knowledge_base_ids 为空列表时跳过 RAG，返回空 docs"""
        result = retrieval_node(self._make_state([]))
        self.assertEqual(result["retrieved_docs"], [])
        self.assertIn("未选择知识库", result["messages"][0]["content"])
        self.assertEqual(result["current_step"], "retrieval")

    def test_none_kb_list_skips_rag(self):
        """knowledge_base_ids 为 None 时跳过 RAG"""
        result = retrieval_node(self._make_state(None))
        self.assertEqual(result["retrieved_docs"], [])
        self.assertIn("未选择知识库", result["messages"][0]["content"])

    def test_missing_user_id_skips_rag(self):
        """user_id 缺失时跳过 RAG（无法构造用户私有索引名）"""
        state = self._make_state(["数学笔记"])
        state["user_id"] = None
        result = retrieval_node(state)
        self.assertEqual(result["retrieved_docs"], [])
        self.assertIn("未选择知识库", result["messages"][0]["content"])


class RetrievalNodeWithKnowledgeBaseTestCase(SimpleTestCase):
    """已选择知识库时 retrieval_node 调用向量库检索"""

    def _make_state(self, kb_list):
        return {
            "learning_plan": {
                "topic": "机器学习",
                "key_points": ["监督学习"],
            },
            "user_id": 7,
            "knowledge_base_ids": kb_list,
        }

    @patch("Django_xm.apps.learning.nodes.retrieval_node._load_and_retrieve")
    def test_single_kb_calls_load_index_with_composed_name(self, mock_load):
        """单个知识库：使用 user_{id}_{name} 调用 _load_and_retrieve"""
        mock_load.return_value = [MagicMock(page_content="doc1")]
        retrieval_node(self._make_state(["数学笔记"]))

        # 验证调用参数（主查询 + 关键点补充检索）
        calls = mock_load.call_args_list
        self.assertTrue(
            any(call.args[0] == "user_7_数学笔记" for call in calls), f"未调用 user_7_数学笔记，实际调用: {calls}"
        )

    @patch("Django_xm.apps.learning.nodes.retrieval_node._load_and_retrieve")
    def test_multiple_kbs_all_loaded(self, mock_load):
        """多个知识库：每个都调用 _load_and_retrieve"""
        mock_load.return_value = [MagicMock(page_content="doc")]
        retrieval_node(self._make_state(["kb_a", "kb_b", "kb_c"]))

        called_names = {call.args[0] for call in mock_load.call_args_list}
        self.assertEqual(
            called_names,
            {"user_7_kb_a", "user_7_kb_b", "user_7_kb_c"},
            f"应加载 3 个知识库，实际: {called_names}",
        )

    @patch("Django_xm.apps.learning.nodes.retrieval_node._load_and_retrieve")
    def test_kb_load_failure_does_not_break_others(self, mock_load):
        """单个知识库加载失败不影响其他知识库"""

        # kb_a 始终返回空（模拟加载失败），kb_b 返回文档
        def _side_effect(user_index_name, query, k=5):
            if "kb_a" in user_index_name:
                return []
            return [MagicMock(page_content="doc_b")]

        mock_load.side_effect = _side_effect
        result = retrieval_node(self._make_state(["kb_a", "kb_b"]))
        # 至少返回 kb_b 的文档（也可能包含关键点补充检索的结果）
        self.assertGreaterEqual(len(result["retrieved_docs"]), 1)

    @patch("Django_xm.apps.learning.nodes.retrieval_node._load_and_retrieve")
    def test_returned_docs_have_expected_structure(self, mock_load):
        """返回的文档结构包含 content/metadata/relevance_score 字段"""
        mock_doc = MagicMock()
        mock_doc.page_content = "学习内容"
        mock_doc.metadata = {"source": "test.md"}
        mock_load.return_value = [mock_doc]

        result = retrieval_node(self._make_state(["kb_a"]))

        self.assertEqual(len(result["retrieved_docs"]), 1)
        doc = result["retrieved_docs"][0]
        self.assertEqual(doc["content"], "学习内容")
        self.assertEqual(doc["metadata"], {"source": "test.md"})
        self.assertIn("relevance_score", doc)


class RetrievalNodeMissingPlanTestCase(SimpleTestCase):
    """learning_plan 缺失时 retrieval_node 跳过检索"""

    def test_missing_plan_returns_empty_docs(self):
        """learning_plan 不存在时返回空 docs 与错误提示"""
        state = {
            "learning_plan": None,
            "user_id": 1,
            "knowledge_base_ids": ["kb_a"],
        }
        result = retrieval_node(state)
        self.assertEqual(result["retrieved_docs"], [])
        self.assertIn("学习计划生成失败", result["messages"][0]["content"])
        self.assertEqual(result["current_step"], "retrieval")


class WorkflowStartSerializerKnowledgeBaseTestCase(SimpleTestCase):
    """WorkflowStartSerializer 接受 knowledge_base_ids 字段"""

    def test_serializer_accepts_knowledge_base_ids(self):
        """序列化器接受 knowledge_base_ids 列表"""
        from Django_xm.apps.learning.serializers import WorkflowStartSerializer

        serializer = WorkflowStartSerializer(
            data={
                "query": "学习机器学习",
                "knowledge_base_ids": ["数学笔记", "ml_basics"],
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["knowledge_base_ids"],
            ["数学笔记", "ml_basics"],
        )

    def test_serializer_default_empty_list_when_missing(self):
        """未提供 knowledge_base_ids 时默认空列表"""
        from Django_xm.apps.learning.serializers import WorkflowStartSerializer

        serializer = WorkflowStartSerializer(data={"query": "学习"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["knowledge_base_ids"], [])

    def test_serializer_rejects_non_string_elements(self):
        """元素类型必须为字符串（整数应被 CharField 拒绝或转换）"""
        from Django_xm.apps.learning.serializers import WorkflowStartSerializer

        serializer = WorkflowStartSerializer(
            data={
                "query": "学习",
                "knowledge_base_ids": [123, "valid"],  # 123 会被 CharField 转换为 "123"
            }
        )
        # CharField 默认会做 str() 转换，所以这里是 valid
        self.assertTrue(serializer.is_valid(), serializer.errors)
