"""跨会话记忆治理测试（fix-review-round3 Task 4.2/4.3）。

覆盖：
- 4.2 淘汰可达：save_session_context 的 store.search 显式传
  limit=max_entries+1——默认 limit=10 < max_entries(20) 时淘汰判定永不触发
  的缺陷修复；超限保存后命名空间条目数收敛回上限、最旧条目被淘汰；
- 4.2 注入窗口配置化：_load_cross_session_context 参与相关性评分的条目数
  由 cross_session_context_window 配置控制（原硬编码 items[:5]）；
- 4.3 摘要合并失败保留累积的领域层行为见 test_save_turn_summary.py
  （test_merge_llm_failure_degrades_to_concatenated_summary 等）。

Store 用真实 InMemoryStore（不经 get_store_provider，不触达真实 PostgresStore）。

运行：
    python manage.py test Django_xm.apps.context_manager.tests.test_cross_session_governance
"""

import unittest
from unittest import mock

from langgraph.store.memory import InMemoryStore

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.manager import ContextManagementConfig, ContextManager

_USER_ID = 556001
_NAMESPACE = (str(_USER_ID), "session_contexts")


def _make_manager(user_id: int = _USER_ID) -> ContextManager:
    return ContextManager(user_id=user_id, config=ContextManagementConfig(cross_session_enabled=True))


def _prepopulate(store: InMemoryStore, count: int, prefix: str = "s") -> None:
    for i in range(count):
        store.put(
            _NAMESPACE,
            f"{prefix}-{i:02d}",
            {
                "session_id": f"{prefix}-{i:02d}",
                "summary": f"历史摘要 {i}",
                "key_entities": [],
                "key_decisions": [],
                "saved_at": float(i),
            },
        )


class CrossSessionEvictionTests(unittest.TestCase):
    """4.2：淘汰判定可达（search 显式 limit）。"""

    def test_save_evicts_oldest_when_over_max_entries(self):
        """存量已达上限时保存新会话：淘汰最旧 1 条，总数收敛回上限。

        修复前（search 未传 limit，默认 10）本用例失败：len(10) < 20 →
        不淘汰 → 总数 21。修复后（limit=max_entries+1）淘汰生效。
        """
        store = InMemoryStore()
        _prepopulate(store, context_settings.cross_session_max_entries)
        manager = _make_manager()

        with mock.patch.object(ContextManager, "_ensure_store", return_value=store):
            self.assertTrue(manager.save_session_context("s-new", "新摘要", [], []))

        items = store.search(_NAMESPACE, limit=context_settings.cross_session_max_entries + 10)
        keys = {item.key for item in items}
        self.assertEqual(len(keys), context_settings.cross_session_max_entries)
        self.assertIn("s-new", keys)
        self.assertNotIn("s-00", keys)  # saved_at 最旧的条目被淘汰
        self.assertIn("s-01", keys)  # 次旧保留

    def test_save_within_limit_does_not_evict(self):
        """存量未达上限：保存不淘汰任何旧条目。"""
        store = InMemoryStore()
        _prepopulate(store, context_settings.cross_session_max_entries - 1)
        manager = _make_manager()

        with mock.patch.object(ContextManager, "_ensure_store", return_value=store):
            self.assertTrue(manager.save_session_context("s-new", "新摘要", [], []))

        items = store.search(_NAMESPACE, limit=context_settings.cross_session_max_entries + 10)
        keys = {item.key for item in items}
        self.assertEqual(len(keys), context_settings.cross_session_max_entries)
        self.assertIn("s-new", keys)
        self.assertIn("s-00", keys)  # 未达上限，最旧条目保留


class CrossSessionContextWindowTests(unittest.TestCase):
    """4.2：注入评分窗口配置化（原硬编码 items[:5]）。"""

    def setUp(self):
        self.store = InMemoryStore()
        # 8 条全部与 query 相关的会话记忆
        for i in range(8):
            self.store.put(
                _NAMESPACE,
                f"w-{i}",
                {
                    "session_id": f"w-{i}",
                    "summary": f"discuss projectX deployment plan step {i}",
                    "key_entities": ["projectX"],
                    "key_decisions": [],
                    "saved_at": float(i),
                },
            )
        self.manager = _make_manager()

    def test_window_limits_scored_items(self):
        """窗口=3 时仅 3 条参与相关性评分；窗口=8 时全部 8 条参与。"""
        with mock.patch.object(ContextManager, "_ensure_store", return_value=self.store), mock.patch.object(
            ContextManager, "_compute_relevance", return_value=0.9
        ) as mock_relevance:
            with mock.patch.object(context_settings, "cross_session_context_window", 3):
                context = self.manager._load_cross_session_context("projectX")
            self.assertEqual(mock_relevance.call_count, 3)

            with mock.patch.object(context_settings, "cross_session_context_window", 8):
                context_full = self.manager._load_cross_session_context("projectX")
            self.assertEqual(mock_relevance.call_count, 3 + 8)

        self.assertTrue(context.startswith("【跨会话上下文】"))
        self.assertTrue(context_full.startswith("【跨会话上下文】"))

    def test_windowed_search_returns_empty_for_unrelated_query(self):
        """无关 query → 词重叠/实体均无命中（Embedding 相似度桩为不可用）→ 不注入。"""
        with mock.patch.object(ContextManager, "_ensure_store", return_value=self.store), mock.patch.object(
            ContextManager, "_compute_embedding_similarity", return_value=None
        ):
            context = self.manager._load_cross_session_context("完全不相关的查询xyz")
        self.assertEqual(context, "")


if __name__ == "__main__":
    unittest.main()
