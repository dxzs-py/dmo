"""Task 5（A7/A8）行为锁定测试：LONG_TERM 判定仅认结构化标记 + 质量评估失败不伪造通过。

覆盖三组契约：
1. A7a ContextCompressionEngine._classify_memory_tier：内容含 "system/decision/
   preference" 等关键词的普通消息 → SHORT_TERM（关键词启发式已删，消息正常压缩）；
   仅 role=system 与显式 memory_tier=long_term → LONG_TERM。
2. A7b ContextManager.mark_long_term / ContextPruner：标记仅结构化；pruner 对
   long_term/pinned/system 消息提供删除保护。
3. A8 evaluate_summary_quality：无原始消息可比对 → passed=False 全零分
   （旧实现伪造 0.7 默认通过，禁止）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_long_term_structured_tier --noinput
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase


def _make_engine() -> object:
    from Django_xm.apps.context_manager.services.compression import ContextCompressionEngine

    # __new__ 跳过 __init__（不构建 EntityExtractor，_classify_memory_tier 不依赖它）
    return ContextCompressionEngine.__new__(ContextCompressionEngine)


class ClassifyMemoryTierStructuredOnlyTests(SimpleTestCase):
    """A7a：_classify_memory_tier 仅认 role=system 与显式 memory_tier 标记。"""

    def test_keyword_content_is_short_term(self):
        from Django_xm.apps.context_manager.services.compression import MemoryTier

        engine = _make_engine()
        cases = [
            {"role": "user", "content": "请帮我做一个 decision 支持系统"},
            {"role": "assistant", "content": "这是系统架构 system 设计说明"},
            {"role": "user", "content": "我的 preference 是深色主题"},
        ]
        for msg in cases:
            self.assertEqual(
                engine._classify_memory_tier(msg),
                MemoryTier.SHORT_TERM,
                f"关键词误标 LONG_TERM: {msg}",
            )

    def test_system_role_and_explicit_tier_are_long_term(self):
        from Django_xm.apps.context_manager.services.compression import MemoryTier

        engine = _make_engine()
        self.assertEqual(engine._classify_memory_tier({"role": "system", "content": "指令"}), MemoryTier.LONG_TERM)
        self.assertEqual(
            engine._classify_memory_tier({"role": "user", "content": "记住这件事", "memory_tier": "long_term"}),
            MemoryTier.LONG_TERM,
        )


class MarkLongTermStructuredOnlyTests(SimpleTestCase):
    """A7b：mark_long_term 不做内容关键词标记（结构化标记唯一实现）。"""

    @staticmethod
    def _make_manager():
        from Django_xm.apps.context_manager.services.manager import ContextManager, ContextManagementConfig

        config = ContextManagementConfig(
            compression_enabled=False,
            knowledge_graph_enabled=False,
            cross_session_enabled=False,
        )
        return ContextManager(config=config)

    def test_keyword_message_not_marked(self):
        manager = self._make_manager()
        messages = [
            {"role": "user", "content": "帮我把这个 decision 记下来"},
            {"role": "assistant", "content": "好的，已确认 system 配置项"},
        ]
        marked = manager.mark_long_term(messages)
        for msg in marked:
            self.assertNotIn("memory_tier", msg, f"关键词误标: {msg}")

    def test_system_and_pre_marked_kept(self):
        manager = self._make_manager()
        messages = [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "普通消息", "memory_tier": "long_term"},
        ]
        marked = manager.mark_long_term(messages)
        self.assertEqual(marked[0]["memory_tier"], "long_term")
        self.assertEqual(marked[1]["memory_tier"], "long_term")


class ContextPrunerProtectionTests(SimpleTestCase):
    """A7b：ContextPruner 对 long_term/pinned/system 消息的删除保护。"""

    @staticmethod
    def _make_pruner():
        from Django_xm.apps.context_manager.services.context_pruner import ContextPruner

        return ContextPruner()

    def test_filter_trivial_keeps_long_term_and_system(self):
        pruner = self._make_pruner()
        messages = [
            {"role": "system", "content": "好"},
            {"role": "user", "content": "好", "memory_tier": "long_term"},
            {"role": "user", "content": "好", "pinned": True},
            {"role": "user", "content": "好"},  # 无保护 → 可过滤
        ]
        filtered, removed = pruner.filter_trivial(messages)
        self.assertEqual(removed, 1)
        self.assertEqual(len(filtered), 3)
        self.assertEqual(filtered[-1]["role"], "user")
        self.assertNotIn("memory_tier", filtered[-1])

    def test_dedup_tool_outputs_keeps_long_term_pair(self):
        pruner = self._make_pruner()
        messages = [
            {"role": "tool", "name": "search", "content": "结果A", "tool_call_id": "call-1", "memory_tier": "long_term"},
            {"role": "tool", "name": "search", "content": "结果B", "tool_call_id": "call-1"},
        ]
        deduped, removed = pruner.dedup_tool_outputs(messages)
        self.assertEqual(removed, 0)
        self.assertEqual(len(deduped), 2)

    def test_dedup_tool_outputs_merges_same_call_id(self):
        """round3 2.1：tool_call_id 存在且相同（同 id 重试）→ 合并保留后者。"""
        pruner = self._make_pruner()
        messages = [
            {"role": "tool", "name": "search", "content": "结果A", "tool_call_id": "call-1"},
            {"role": "tool", "name": "search", "content": "结果B", "tool_call_id": "call-1"},
        ]
        deduped, removed = pruner.dedup_tool_outputs(messages)
        self.assertEqual(removed, 1)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["content"], "结果B")

    def test_dedup_tool_outputs_keeps_parallel_different_call_ids(self):
        """round3 2.1：3 个并行同名工具不同 tool_call_id → 全部保留（引用完整性红线）。"""
        pruner = self._make_pruner()
        messages = [
            {"role": "tool", "name": "web_search", "content": "结果A", "tool_call_id": "call-1"},
            {"role": "tool", "name": "web_search", "content": "结果B", "tool_call_id": "call-2"},
            {"role": "tool", "name": "web_search", "content": "结果C", "tool_call_id": "call-3"},
        ]
        deduped, removed = pruner.dedup_tool_outputs(messages)
        self.assertEqual(removed, 0)
        self.assertEqual(len(deduped), 3)
        self.assertEqual([m["tool_call_id"] for m in deduped], ["call-1", "call-2", "call-3"])

    def test_dedup_tool_outputs_keeps_missing_call_id(self):
        """round3 2.1：任一侧缺失 tool_call_id → 不合并（无法确认非独立调用）。"""
        pruner = self._make_pruner()
        messages = [
            {"role": "tool", "name": "search", "content": "结果A", "tool_call_id": "call-1"},
            {"role": "tool", "name": "search", "content": "结果B"},
        ]
        deduped, removed = pruner.dedup_tool_outputs(messages)
        self.assertEqual(removed, 0)
        self.assertEqual(len(deduped), 2)

    def test_prune_by_relevance_keeps_long_term(self):
        pruner = self._make_pruner()
        query = "完全无关的查询词"
        messages = [
            {"role": "user", "content": "历史消息甲", "memory_tier": "long_term"},
            {"role": "user", "content": "历史消息乙"},
            {"role": "user", "content": "历史消息丙"},
            {"role": "user", "content": "历史消息丁"},
        ]
        result, removed = pruner.prune_by_relevance(messages, query, keep_count=2)
        self.assertIn(messages[0], result, "long_term 消息必须被保护保留")
        self.assertEqual(removed, 2)


class EvaluateSummaryQualityNoMessageTests(SimpleTestCase):
    """A8：无原始消息可比对 → passed=False 全零分（不伪造 0.7 默认通过）。"""

    def test_empty_messages_returns_not_passed(self):
        from Django_xm.apps.context_manager.services.compression import ContextCompressionEngine

        engine = ContextCompressionEngine.__new__(ContextCompressionEngine)
        result = engine.evaluate_summary_quality([], "一份看起来不错的摘要")
        self.assertFalse(result.passed)
        self.assertEqual(result.overall, 0.0)
        self.assertEqual(result.completeness, 0.0)

    def test_empty_summary_returns_not_passed(self):
        from Django_xm.apps.context_manager.services.compression import ContextCompressionEngine

        engine = ContextCompressionEngine.__new__(ContextCompressionEngine)
        result = engine.evaluate_summary_quality([{"role": "user", "content": "问题"}], "  ")
        self.assertFalse(result.passed)
