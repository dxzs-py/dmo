"""Task12（A10~A14）修复回归测试。

覆盖：
- A10：prune_by_relevance 保时序（分数只决定去留）+ embedding 批量化降级路径；
- A12：trim_messages_to_budget 最终裁剪兜底（保护 system/pinned/long_term 与最近 N 条，
  BudgetTrimResult.trimmed 标记）+ TokenEstimator 未知模型窗口告警；
- A13：KnowledgeGraphStore 空/空白 query 直接返回空上下文；分 key 存储布局
  （entity:/relation: 前缀 + merge 仅写增量 + 旧版单 key 快照只读兼容）；
- A14：_merge_unique_values 新值优先 + 超限保序淘汰尾部；
- A11：preserve_tool_results 接线（role=tool 消息不截断进摘要输入）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_task12_p2_fixes --settings=Django_xm.settings.test
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.context_manager.services.compression import (
    CompressionConfig,
    CompressionStrategy,
    ContextCompressionEngine,
)
from Django_xm.apps.context_manager.services.context_pruner import ContextPruner
from Django_xm.apps.context_manager.services.knowledge_graph import (
    _KG_ENTITY_KEY_PREFIX,
    Entity,
    KnowledgeGraphStore,
    clear_graph_cache,
)
from Django_xm.apps.context_manager.services.manager import ContextManager
from Django_xm.apps.context_manager.services.token_budget import BudgetTrimResult, trim_messages_to_budget
from Django_xm.common.token_estimator import TokenEstimator


class PruneByRelevanceOrderTests(unittest.TestCase):
    """A10：语义相关性筛选必须保持原消息时序。"""

    def _make_pruner(self) -> ContextPruner:
        return ContextPruner()

    def test_keeps_original_message_order(self):
        """被保留消息（含受保护消息）按原位置输出，不按分数重排。"""
        pruner = self._make_pruner()
        messages = [
            {"role": "user", "content": "先聊 Python 基础语法"},
            {"role": "assistant", "content": "好的，Python 基础语法包括变量与循环"},
            {"role": "user", "content": "再聊聊向量数据库 faiss 的使用"},
            {"role": "assistant", "content": "faiss 是高效的向量检索库"},
            {"role": "user", "content": "最后帮我看看 kubernetes 部署配置"},
            {"role": "assistant", "content": "kubernetes 部署需要编写 yaml"},
        ]
        # keep_count=4：保留 4 条（无受保护消息），2 条被移除
        result, removed = pruner.prune_by_relevance(messages, "faiss 向量检索", keep_count=4)

        self.assertEqual(removed, 2)
        # 输出必须保持原消息相对顺序
        positions = [messages.index(m) for m in result]
        self.assertEqual(positions, sorted(positions))
        # 与 query 强相关的 faiss 消息对必须保留
        self.assertIn(messages[2], result)
        self.assertIn(messages[3], result)

    def test_protected_messages_stay_in_place(self):
        """long_term/pinned/system 消息原位保留，普通消息按相关性筛选。"""
        pruner = self._make_pruner()
        messages = [
            {"role": "system", "content": "系统提示词"},
            {"role": "user", "content": "普通消息甲"},
            {"role": "user", "content": "python 机器学习相关讨论"},
            {"role": "user", "content": "普通消息乙", "pinned": True},
            {"role": "user", "content": "faiss 向量检索讨论"},
        ]
        result, _removed = pruner.prune_by_relevance(messages, "python 机器学习", keep_count=3)

        self.assertIn(messages[0], result)  # system 受保护
        self.assertIn(messages[3], result)  # pinned 受保护
        self.assertIn(messages[2], result)  # 与 query 强相关
        positions = [messages.index(m) for m in result]
        self.assertEqual(positions, sorted(positions))

    def test_embedding_provider_missing_falls_back_to_word_overlap(self):
        """Embedding 提供者未注册 → 词重叠回退路径可用（不抛异常）。"""
        pruner = self._make_pruner()
        messages = [
            {"role": "user", "content": "python 相关讨论"},
            {"role": "user", "content": "完全无关的消息"},
            {"role": "user", "content": "another python related message"},
            {"role": "user", "content": "无关消息乙"},
        ]
        with mock.patch(
            "Django_xm.apps.context_manager.services.runtime_providers.get_embedding_provider",
            return_value=None,
        ):
            result, removed = pruner.prune_by_relevance(messages, "python", keep_count=2)

        self.assertEqual(removed, 2)
        self.assertIn(messages[0], result)
        self.assertIn(messages[2], result)


class TrimMessagesToBudgetTests(unittest.TestCase):
    """A12：Token 预算最终裁剪兜底。"""

    def test_within_budget_returns_untouched(self):
        messages = [{"role": "user", "content": "短消息"}]
        trimmed, result = trim_messages_to_budget(messages, budget_tokens=10_000, keep_recent=2)

        self.assertIs(trimmed, messages)
        self.assertIsInstance(result, BudgetTrimResult)
        self.assertFalse(result.trimmed)

    def test_trims_oldest_first_and_keeps_protected_and_recent(self):
        messages = [{"role": "user", "content": "旧消息内容" * 50} for _ in range(6)]
        messages.insert(0, {"role": "system", "content": "系统提示词" * 5})
        budget = TokenEstimator.estimate_dict_messages(messages) // 2

        trimmed, result = trim_messages_to_budget(messages, budget_tokens=budget, keep_recent=2)

        self.assertTrue(result.trimmed)
        self.assertGreater(result.removed_count, 0)
        self.assertLessEqual(result.trimmed_tokens, budget)
        # system 受保护 + 最近 2 条固定保留
        self.assertIn(messages[0], trimmed)
        self.assertIn(messages[-1], trimmed)
        self.assertIn(messages[-2], trimmed)
        # 保序：裁剪后仍是原消息子序列
        positions = [messages.index(m) for m in trimmed]
        self.assertEqual(positions, sorted(positions))

    def test_zero_budget_is_noop(self):
        messages = [{"role": "user", "content": "内容"}]
        trimmed, result = trim_messages_to_budget(messages, budget_tokens=0, keep_recent=2)

        self.assertIs(trimmed, messages)
        self.assertFalse(result.trimmed)

    def test_all_protected_trims_nothing(self):
        messages = [{"role": "system", "content": "系统提示词" * 100}]
        trimmed, result = trim_messages_to_budget(messages, budget_tokens=10, keep_recent=0)

        self.assertIs(trimmed, messages)
        self.assertFalse(result.trimmed)


class TokenEstimatorModelLimitsTests(unittest.TestCase):
    """A12：模型窗口表扩充与未知模型告警。"""

    def test_mainstream_tiers_present(self):
        self.assertEqual(TokenEstimator.get_model_limit("deepseek-chat"), 128000)
        self.assertEqual(TokenEstimator.get_model_limit("deepseek-reasoner"), 128000)
        self.assertEqual(TokenEstimator.get_model_limit("claude-sonnet-4-5-20250929"), 200000)
        self.assertEqual(TokenEstimator.get_model_limit("gemini-2.5-pro"), 1048576)
        self.assertEqual(TokenEstimator.get_model_limit("qwen-plus"), 131072)

    def test_unknown_model_warns_and_returns_default(self):
        with self.assertLogs("Django_xm.common.token_estimator", level="WARNING") as logs:
            limit = TokenEstimator.get_model_limit("totally-unknown-model-xyz")

        self.assertEqual(limit, 128000)
        self.assertTrue(any("未知模型" in message for message in logs.output))

    def test_specific_prefix_wins_over_generic(self):
        """更具体的前缀优先命中（gpt-4o-mini 不得被 gpt-4 的 8192 截胡）。"""
        self.assertEqual(TokenEstimator.get_model_limit("gpt-4o-mini"), 128000)
        self.assertEqual(TokenEstimator.get_model_limit("o1-mini"), 128000)


class KnowledgeGraphStoreTests(unittest.TestCase):
    """A13：KG 空 query 拦截 + 分 key 存储 + 增量写入。"""

    def setUp(self):
        # 4.6 图缓存为模块级共享（按 user 键）：用例间清理，防跨用例串数据
        clear_graph_cache()

    class _FakeItem:
        def __init__(self, key: str, value):
            self.key = key
            self.value = value

    class _FakeStore:
        """最小 LangGraph Store 桩：put/get/search/delete + put 计数（验证增量写入）。"""

        def __init__(self):
            self.items = {}
            self.put_count = 0

        def put(self, namespace, key, value):
            self.put_count += 1
            self.items.setdefault(tuple(namespace), {})[key] = KnowledgeGraphStoreTests._FakeItem(key, value)

        def get(self, namespace, key):
            return self.items.get(tuple(namespace), {}).get(key)

        def search(self, namespace, limit=10):
            return list(self.items.get(tuple(namespace), {}).values())[:limit]

        def delete(self, namespace, key):
            self.items.get(tuple(namespace), {}).pop(key, None)

    def _make_entity(self, name: str) -> Entity:
        return Entity(name=name, entity_type="technology", confidence=0.9, first_seen=1.0, last_seen=1.0)

    def _make_store(self) -> KnowledgeGraphStore:
        return KnowledgeGraphStore(store=self._FakeStore())

    def test_empty_query_returns_empty_context(self):
        store = self._make_store()
        store.merge_graph(1, [self._make_entity("python")], [])

        self.assertEqual(store.get_related_context(1, ""), "")
        self.assertEqual(store.get_related_context(1, "   "), "")

    def test_per_entity_key_layout(self):
        store = self._make_store()
        store.merge_graph(1, [self._make_entity("python"), self._make_entity("django")], [])

        namespace_items = store._store.items[(str(1), "knowledge_graph")]
        self.assertIn(f"{_KG_ENTITY_KEY_PREFIX}python", namespace_items)
        self.assertIn(f"{_KG_ENTITY_KEY_PREFIX}django", namespace_items)
        self.assertNotIn("graph", namespace_items)  # 新写入不再落旧版单 key

    def test_merge_only_writes_delta(self):
        store = self._make_store()
        store.merge_graph(1, [self._make_entity("python")], [])
        first_put_count = store._store.put_count

        # 第二轮只提及新实体：不应重写已有实体
        clear_graph_cache()  # 模拟跨实例/重启后重新 load
        store.merge_graph(1, [self._make_entity("django")], [])

        self.assertEqual(store._store.put_count - first_put_count, 1)
        entities, _ = store.load_graph(1)
        self.assertEqual({e.name for e in entities}, {"python", "django"})

    def test_legacy_snapshot_read_compat(self):
        """旧版单 key 快照在无分 key 条目时仍可读（存量兼容）；有分 key 时不读旧快照。"""
        fake_store = self._FakeStore()
        legacy_data = {
            "entities": {"python": self._make_entity("python").to_dict()},
            "relations": {},
            "updated_at": 1.0,
        }
        fake_store.put((str(1), "knowledge_graph"), "graph", legacy_data)
        store = KnowledgeGraphStore(store=fake_store)

        entities, _ = store.load_graph(1)
        self.assertEqual([e.name for e in entities], ["python"])

        # 有分 key 条目后，旧快照不再参与（新实例冷加载验证，避免进程缓存干扰）
        clear_graph_cache()
        store.merge_graph(1, [self._make_entity("vue")], [])
        fresh_store = KnowledgeGraphStore(store=fake_store)
        clear_graph_cache()  # 冷加载：模块级缓存清空后从 Store 重建
        entities, _ = fresh_store.load_graph(1)
        self.assertEqual({e.name for e in entities}, {"vue"})


class MergeUniqueValuesTests(unittest.TestCase):
    """A14：记忆合并新值优先。"""

    def test_new_values_come_first(self):
        merged = ContextManager._merge_unique_values(
            ["Vue3", "Pinia"],
            ["Router 守卫", "Vue3"],
            limit=10,
        )
        self.assertEqual(merged, ["Router 守卫", "Vue3", "Pinia"])

    def test_limit_evicts_oldest_tail(self):
        merged = ContextManager._merge_unique_values(
            ["旧偏好甲", "旧偏好乙"],
            ["新偏好丙"],
            limit=2,
        )
        self.assertEqual(merged, ["新偏好丙", "旧偏好甲"])

    def test_new_value_text_wins_on_duplicate(self):
        merged = ContextManager._merge_unique_values(
            ["vue3"],
            ["Vue3"],
            limit=10,
        )
        self.assertEqual(merged, ["Vue3"])


class PreserveToolResultsTests(unittest.TestCase):
    """A11：preserve_tool_results 接线——role=tool 消息不截断进摘要输入。"""

    def _long_tool_output(self) -> str:
        return "工具输出" * 200  # 800 字符，超过 400 截断阈值

    def test_tool_results_not_truncated_when_preserved(self):
        config = CompressionConfig(strategy=CompressionStrategy.HYBRID, preserve_tool_results=True)
        engine = ContextCompressionEngine(config)
        messages = [{"role": "tool", "name": "search", "content": self._long_tool_output()}]

        formatted = engine._format_messages(messages)

        self.assertIn(self._long_tool_output(), formatted)

    def test_other_roles_truncated_by_default(self):
        config = CompressionConfig(strategy=CompressionStrategy.HYBRID)
        engine = ContextCompressionEngine(config)
        messages = [{"role": "user", "content": "用户消息" * 200}]

        formatted = engine._format_messages(messages)

        self.assertNotIn("用户消息" * 200, formatted)
        self.assertIn("...", formatted)

    def test_tool_results_truncated_when_disabled(self):
        config = CompressionConfig(strategy=CompressionStrategy.HYBRID, preserve_tool_results=False)
        engine = ContextCompressionEngine(config)
        messages = [{"role": "tool", "name": "search", "content": self._long_tool_output()}]

        formatted = engine._format_messages(messages)

        self.assertNotIn(self._long_tool_output(), formatted)


if __name__ == "__main__":
    unittest.main()
