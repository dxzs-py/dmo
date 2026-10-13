"""1.3 KG 数据入口接线测试（fix-review-round2）。

背景：ContextKnowledgeGraph.process_conversation（实体/关系规则抽取 + Store 增量
合并）此前全仓零调用，知识图谱恒空；本次在 chat finalizer 跨会话记忆写入链路
（chat_executor_core._save_cross_session_memory，见
test_cross_session_memory_wiring.py 的接线断言）触发。本文件覆盖领域层行为：

1. process_conversation 写入后 get_context_for_query 能返回实体上下文（写→读闭环）；
2. 无命中 query 返回空串——「无命中回退 mention_count top3」的无关注入已删除；
3. ContextManager.process_conversation 委托与降级：kg 未启用 → False、
   图谱处理异常吞没 → False（不阻塞调用方收尾主链路）。

Store 用 InMemoryStore（经构造参数注入，不经 get_store_provider，不触达真实
PostgresStore）。

运行：
    python manage.py test Django_xm.apps.context_manager.tests.test_kg_wiring
"""

import unittest
from unittest import mock

from langgraph.store.memory import InMemoryStore

from Django_xm.apps.context_manager.services.knowledge_graph import (
    ContextKnowledgeGraph,
    clear_graph_cache,
)
from Django_xm.apps.context_manager.services.manager import ContextManager, ContextManagementConfig

_TURN_MESSAGES = [
    {"role": "user", "content": "我喜欢『Vue』，我们的项目用 python 和 django。"},
    {"role": "assistant", "content": "建议使用 Vue 组合式 API 组织逻辑。"},
]


class KnowledgeGraphWriteReadTests(unittest.TestCase):
    """process_conversation 写入 → get_context_for_query 读取闭环。"""

    def setUp(self):
        # 4.6 图缓存为模块级共享（按 user 键）：用例间清理，防跨用例串数据
        clear_graph_cache()

    def test_process_conversation_returns_merged_graph(self):
        kg = ContextKnowledgeGraph(store=InMemoryStore())
        entities, relations = kg.process_conversation(1, _TURN_MESSAGES)

        self.assertTrue(entities)
        names = {e.name for e in entities}
        # 4.1① 裸引号泛抽取删除后，"Vue" 实体来自 tech 关键词匹配（名称为小写关键词）
        self.assertIn("vue", names)

    def test_written_entities_are_retrievable_by_query(self):
        kg = ContextKnowledgeGraph(store=InMemoryStore())
        kg.process_conversation(1, _TURN_MESSAGES)

        context = kg.get_context_for_query(1, "Vue 项目怎么组织")
        self.assertIn("【知识图谱上下文·来源：对话记忆】", context)
        self.assertIn("vue", context)

    def test_incremental_merge_accumulates_mentions(self):
        kg = ContextKnowledgeGraph(store=InMemoryStore())
        kg.process_conversation(1, _TURN_MESSAGES)
        first_entities, _ = kg.get_full_graph(1)
        first_count = next(e.mention_count for e in first_entities if e.name == "vue")

        kg.process_conversation(1, _TURN_MESSAGES)
        second_entities, _ = kg.get_full_graph(1)
        second_count = next(e.mention_count for e in second_entities if e.name == "vue")

        self.assertEqual(second_count, first_count * 2)


class NoMatchNoInjectionTests(unittest.TestCase):
    """无命中回退 mention_count top3 已删除：query 与图谱无关时不注入。"""

    def setUp(self):
        clear_graph_cache()

    def test_no_match_query_returns_empty(self):
        kg = ContextKnowledgeGraph(store=InMemoryStore())
        kg.process_conversation(1, _TURN_MESSAGES)

        context = kg.get_context_for_query(1, "完全不相关的查询内容xyz")
        self.assertEqual(context, "")

    def test_empty_graph_returns_empty(self):
        kg = ContextKnowledgeGraph(store=InMemoryStore())
        self.assertEqual(kg.get_context_for_query(1, "任何查询"), "")


class ManagerProcessConversationTests(unittest.TestCase):
    """ContextManager.process_conversation 公共入口：委托与降级语义。"""

    def setUp(self):
        clear_graph_cache()

    def _make_manager(self, knowledge_graph_enabled=True) -> ContextManager:
        config = ContextManagementConfig(
            knowledge_graph_enabled=knowledge_graph_enabled,
            compression_enabled=False,
        )
        return ContextManager(user_id=1, config=config, store=InMemoryStore())

    def test_delegates_to_knowledge_graph(self):
        manager = self._make_manager()
        self.assertTrue(manager.process_conversation(1, _TURN_MESSAGES))
        context = manager._knowledge_graph.get_context_for_query(1, "Vue 项目怎么组织")
        self.assertIn("【知识图谱上下文·来源：对话记忆】", context)

    def test_disabled_knowledge_graph_returns_false(self):
        manager = self._make_manager(knowledge_graph_enabled=False)
        self.assertFalse(manager.process_conversation(1, _TURN_MESSAGES))

    def test_missing_user_id_returns_false(self):
        manager = self._make_manager()
        self.assertFalse(manager.process_conversation(None, _TURN_MESSAGES))

    def test_exception_swallowed_returns_false(self):
        manager = self._make_manager()
        with mock.patch.object(
            ContextKnowledgeGraph, "process_conversation", side_effect=RuntimeError("store down")
        ):
            self.assertFalse(manager.process_conversation(1, _TURN_MESSAGES))
