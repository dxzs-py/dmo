"""双压缩路径表征测试（ARCH-07 先表征后合并）。

在合并两条并行压缩路径之前，锁定各自的关键行为，合并后必须全绿：

- 路径 A（chat 入口，dict 语义）：
  ContextManager.process_messages → ContextCompressionEngine.compress。
  锁定：阈值触发、保留最近 N 条、摘要生成调用、预算分区记录。
- 路径 B（agent 入口，BaseMessage 语义）：ContextManagerMiddleware.process_messages →
  ProgressiveCompressor（内部持有 ContextCompressionEngine）。
  锁定：BaseMessage 输入输出类型、Level 2 摘要触发、Level 4 激进压缩
  （无 LLM、实体/决策上下文、保留最近 N 条、query 兜底）、双路径共用同一引擎类。

mock 点说明：LLM 调用统一在引擎实例上 patch _get_compression_model，
不发起真实请求；质量评估 patch evaluate_summary_quality，不依赖 DB / LLM。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_compression_characterization
"""

import os
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware
from Django_xm.apps.context_manager.services.compression import (
    CompressionStrategy,
    ContextCompressionEngine,
    SummaryQuality,
)
from Django_xm.apps.context_manager.services.manager import ContextManager, ContextManagementConfig
from Django_xm.apps.context_manager.services.progressive_compressor import CompressionLevel, ProgressiveCompressor

_BIG_CONTENT = "hello world " * 30


def _fake_llm(content: str) -> mock.MagicMock:
    """构造返回固定 content 的假 LLM 模型。"""
    response = mock.MagicMock()
    response.content = content
    model = mock.MagicMock()
    model.invoke.return_value = response
    return model


def _make_manager(keep_recent: int = 2) -> ContextManager:
    """构造路径 A 的 ContextManager（关闭知识图谱/跨会话，调小触发上限）。"""
    config = ContextManagementConfig(
        compression_enabled=True,
        compression_strategy="hybrid",
        compression_threshold_ratio=0.8,
        compression_keep_recent=keep_recent,
        compression_summary_max_length=200,
        knowledge_graph_enabled=False,
        cross_session_enabled=False,
        model_name="",
    )
    manager = ContextManager(user_id=None, config=config, store=None, thread_id=None)
    assert manager._compression_engine is not None
    # 调小触发上限（trigger = 100 * 0.8 = 80 token），避免构造超长文本
    manager._compression_engine.config.max_context_tokens = 100
    return manager


def _patch_engine_llm(engine: ContextCompressionEngine, content: str):
    """同时 patch 引擎的摘要模型与质量评估，返回 (patcher 上下文, fake_model)。"""
    fake_model = _fake_llm(content)
    quality = SummaryQuality(completeness=0.9, accuracy=0.9, conciseness=0.9, overall=0.9, passed=True)
    return (
        (
            mock.patch.object(engine, "_get_compression_model", return_value=fake_model),
            mock.patch.object(engine, "evaluate_summary_quality", return_value=quality),
        ),
        fake_model,
    )


class PathAContextManagerCompressionTests(unittest.TestCase):
    """路径 A：ContextManager.process_messages（dict 语义，chat 入口）。"""

    def test_threshold_trigger_and_strategy(self) -> None:
        """超阈值触发压缩：compressed=True、strategy=hybrid、输出保留最近 N 条。"""
        manager = _make_manager(keep_recent=2)
        engine = manager._compression_engine
        assert engine is not None
        patchers, fake_model = _patch_engine_llm(engine, "路径A摘要")

        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": _BIG_CONTENT} for i in range(4)]
        with patchers[0], patchers[1]:
            compressed, metadata = manager.process_messages(messages)

        self.assertTrue(metadata["compression"]["compressed"])
        self.assertEqual(metadata["compression"]["strategy"], CompressionStrategy.HYBRID.value)
        # 1 条摘要 system 消息 + 最近 keep_recent=2 条
        self.assertEqual(len(compressed), 3)
        summary_msgs = [m for m in compressed if m.get("role") == "system"]
        self.assertEqual(len(summary_msgs), 1)
        self.assertIn("【对话摘要】", summary_msgs[0]["content"])
        # 保留最近 2 条原始消息（内容原样）
        self.assertEqual([m["content"] for m in compressed[-2:]] , [_BIG_CONTENT, _BIG_CONTENT])
        # 摘要生成调用恰好 1 次
        fake_model.invoke.assert_called_once()

    def test_below_threshold_returns_original(self) -> None:
        """未超阈值：不压缩（mark_long_term 会重建列表，此处锁定内容与标记不变）。"""
        manager = _make_manager(keep_recent=2)
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        compressed, metadata = manager.process_messages(messages)
        self.assertFalse(metadata["compression"]["compressed"])
        self.assertEqual(compressed, messages)

    def test_budget_usage_recorded_in_structured_context(self) -> None:
        """预算扣减记录：build_structured_context 分区记录 history 用量。"""
        manager = _make_manager(keep_recent=2)
        messages = [
            {"role": "user", "content": _BIG_CONTENT},
            {"role": "assistant", "content": _BIG_CONTENT},
        ]
        result = manager.build_structured_context(messages=messages, query="测试问题", mode="full")
        budget_usage = result["metadata"]["budget_usage"]
        self.assertGreater(budget_usage["history"]["used"], 0)


class PathBProgressiveCompressionTests(unittest.TestCase):
    """路径 B：ContextManagerMiddleware.process_messages（BaseMessage 语义，agent 入口）。"""

    def _make_middleware(self) -> ContextManagerMiddleware:
        return ContextManagerMiddleware(model_name="", user_id=None, store=None, thread_id=None)

    def test_both_paths_share_single_engine_class(self) -> None:
        """表征：middleware → ProgressiveCompressor 内部持有统一引擎 ContextCompressionEngine。"""
        mw = self._make_middleware()
        compressor = mw._progressive_compressor
        self.assertIsInstance(compressor, ProgressiveCompressor)
        self.assertIsInstance(compressor._engine, ContextCompressionEngine)

    def test_low_load_returns_base_messages(self) -> None:
        """低负载（Level 1）：BaseMessage 输入 → BaseMessage 输出，不调 LLM。"""
        mw = self._make_middleware()
        compressor = mw._progressive_compressor
        llm_getter = mock.MagicMock()
        with mock.patch.object(compressor._engine, "_get_compression_model", llm_getter):
            messages = [HumanMessage(content="hi"), AIMessage(content="hello there")]
            out = mw.process_messages(messages)

        llm_getter.assert_not_called()
        self.assertTrue(all(isinstance(m, BaseMessage) for m in out))
        self.assertLessEqual(len(out), 2)

    def test_level2_summary_and_keep_recent(self) -> None:
        """Level 2 摘要压缩：注入低阈值强制触发，摘要 system 消息 + 最近 6 条保留。"""
        mw = self._make_middleware()
        compressor = mw._progressive_compressor
        fake_model = _fake_llm("路径B摘要")
        # 强制 Level 2：level 2 阈值归零（任何占比都命中）
        compressor._level_thresholds[CompressionLevel.LEVEL_2_SUMMARY] = 0.0
        with mock.patch.object(compressor._engine, "_get_compression_model", return_value=fake_model):
            big = [HumanMessage(content=_BIG_CONTENT) for _ in range(4)] + [
                AIMessage(content=_BIG_CONTENT) for _ in range(4)
            ]
            out = mw.process_messages(big)

        fake_model.invoke.assert_called()
        self.assertTrue(all(isinstance(m, BaseMessage) for m in out))
        # 1 条摘要 system 消息 + keep_recent=6 条
        self.assertEqual(len(out), 7)
        summary_msgs = [m for m in out if isinstance(m, BaseMessage) and m.type == "system"]
        self.assertEqual(len(summary_msgs), 1)
        self.assertIn("【对话摘要】", summary_msgs[0].content)
        # 保留最近 6 条原始消息（内容原样、顺序一致）
        self.assertEqual(
            [m.content for m in out[-6:]],
            [_BIG_CONTENT] * 6,
        )

    def test_level4_aggressive_no_llm_and_context_block(self) -> None:
        """Level 4 激进压缩：不调 LLM 摘要，实体/决策上下文块 + 最近 N 条保留。"""
        mw = self._make_middleware()
        compressor = mw._progressive_compressor
        llm_getter = mock.MagicMock()
        compressor._level_thresholds[CompressionLevel.LEVEL_4_AGGRESSIVE] = 0.0
        # 实体/决策可命中的中文内容（L4 不调 LLM，仅规则抽取）
        user_content = "使用 PostgreSQL 数据库做向量存储"
        assistant_content = "建议：使用 pgvector 扩展加速检索"
        big = [HumanMessage(content=user_content) for _ in range(4)] + [
            AIMessage(content=assistant_content) for _ in range(4)
        ]
        with mock.patch.object(compressor._engine, "_get_compression_model", llm_getter):
            out = mw.process_messages(big)

        llm_getter.assert_not_called()
        self.assertTrue(all(isinstance(m, BaseMessage) for m in out))
        # 上下文 system 块（实体/决策）+ 最近 6 条原始消息
        system_msgs = [m for m in out if m.type == "system"]
        self.assertEqual(len(system_msgs), 1)
        self.assertIn("【关键实体】", system_msgs[0].content)
        self.assertEqual([m.content for m in out[-6:]], [user_content] * 2 + [assistant_content] * 4)

    def test_level4_query_fallback_injected_when_missing(self) -> None:
        """Level 4 query 兜底：query 不在压缩结果中时追加 user 消息。"""
        compressor = ProgressiveCompressor(model_name="")
        compressor._level_thresholds[CompressionLevel.LEVEL_4_AGGRESSIVE] = 0.0
        big = [{"role": "user", "content": _BIG_CONTENT} for _ in range(8)]
        out, result = compressor.compress(big, query="全新问题")
        self.assertIs(result.level, CompressionLevel.LEVEL_4_AGGRESSIVE)
        self.assertEqual(out[-1], {"role": "user", "content": "全新问题"})

    def test_level4_compress_api_keeps_result_fields(self) -> None:
        """Level 4 结果字段：level/strategy_used/aggressive 语义保持。"""
        compressor = ProgressiveCompressor(model_name="")
        compressor._level_thresholds[CompressionLevel.LEVEL_4_AGGRESSIVE] = 0.0
        big = [{"role": "user", "content": _BIG_CONTENT} for _ in range(8)]
        _, result = compressor.compress(big, query="最终问题")
        self.assertIs(result.level, CompressionLevel.LEVEL_4_AGGRESSIVE)
        self.assertEqual(result.strategy_used, "aggressive")
        self.assertGreater(result.compression_ratio, 0.0)
        self.assertTrue(result.key_entities or result.key_decisions or result.messages)


if __name__ == "__main__":
    unittest.main()
