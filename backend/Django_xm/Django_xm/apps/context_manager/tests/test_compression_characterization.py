"""压缩路径表征测试（Task1 官方语义重构后）。

路径边界重构说明：
- agent 入口的 state 压缩已整体替换为官方 langchain SummarizationMiddleware
  （组合于 ContextManagerMiddleware，见 test_middleware_summarization.py），
  原 ProgressiveCompressor / middleware.process_messages 路径已删除；
- 本文件锁定仍由自研引擎 ContextCompressionEngine.compress 承载的
  ContextManager 路径（/compact 与 build_structured_context，prompt 文本语义）：
  阈值触发、保留最近 N 条、摘要生成调用、预算分区记录。

mock 点说明：LLM 调用统一在引擎实例上 patch _get_compression_model，
不发起真实请求；质量评估 patch evaluate_summary_quality，不依赖 DB / LLM。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_compression_characterization
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.context_manager.services.compression import (
    CompressionStrategy,
    ContextCompressionEngine,
    SummaryQuality,
)
from Django_xm.apps.context_manager.services.manager import ContextManager, ContextManagementConfig

_BIG_CONTENT = "hello world " * 30


def _fake_llm(content: str) -> mock.MagicMock:
    """构造返回固定 content 的假 LLM 模型。"""
    response = mock.MagicMock()
    response.content = content
    model = mock.MagicMock()
    model.invoke.return_value = response
    return model


def _make_engine(keep_recent: int = 2) -> ContextCompressionEngine:
    """构造路径 A 的压缩引擎（调小触发上限，避免构造超长文本）。"""
    engine = ContextCompressionEngine(_make_config(keep_recent))
    engine.config.max_context_tokens = 100
    return engine


def _make_config(keep_recent: int):
    from Django_xm.apps.context_manager.services.compression import CompressionConfig

    return CompressionConfig(
        max_context_tokens=100,
        token_threshold_ratio=0.8,
        keep_recent_messages=keep_recent,
        summary_max_length=200,
        strategy=CompressionStrategy.HYBRID,
    )


def _make_manager(keep_recent: int = 2) -> ContextManager:
    """构造 ContextManager（关闭知识图谱/跨会话，调小触发上限）。"""
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


class EngineCompressTests(unittest.TestCase):
    """ContextCompressionEngine.compress（dict 语义，/compact 与结构化上下文路径）。"""

    def test_threshold_trigger_and_strategy(self) -> None:
        """超阈值触发压缩：compressed=True、strategy=hybrid、输出保留最近 N 条。"""
        engine = _make_engine(keep_recent=2)
        patchers, fake_model = _patch_engine_llm(engine, "引擎摘要")

        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": _BIG_CONTENT} for i in range(4)]
        with patchers[0], patchers[1]:
            compressed, result = engine.compress(messages)

        self.assertTrue(result.compressed)
        self.assertEqual(result.strategy_used, CompressionStrategy.HYBRID)
        # 1 条摘要 system 消息 + 最近 keep_recent=2 条
        self.assertEqual(len(compressed), 3)
        summary_msgs = [m for m in compressed if m.get("role") == "system"]
        self.assertEqual(len(summary_msgs), 1)
        self.assertIn("【对话摘要】", summary_msgs[0]["content"])
        # 保留最近 2 条原始消息（内容原样）
        self.assertEqual([m["content"] for m in compressed[-2:]], [_BIG_CONTENT, _BIG_CONTENT])
        # 摘要生成调用恰好 1 次
        fake_model.invoke.assert_called_once()
        # token 估算随压缩结果重算：输出必须小于输入
        self.assertLess(result.compressed_token_estimate, result.original_token_estimate)

    def test_below_threshold_returns_original(self) -> None:
        """未超阈值：不压缩，原样返回。"""
        engine = _make_engine(keep_recent=2)
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        compressed, result = engine.compress(messages)
        self.assertFalse(result.compressed)
        self.assertEqual(compressed, messages)

    def test_quality_gate_failure_rebuilds_degraded_content(self) -> None:
        """质量门禁不合格：降级摘要【原文关键片段】真实进入输出消息列表。"""
        engine = _make_engine(keep_recent=2)
        fake_model = _fake_llm("低质量摘要")
        bad_quality = SummaryQuality(completeness=0.1, accuracy=0.1, conciseness=0.1, overall=0.1, passed=False)
        messages = [{"role": "user", "content": _BIG_CONTENT} for _ in range(4)]
        with (
            mock.patch.object(engine, "_get_compression_model", return_value=fake_model),
            mock.patch.object(engine, "evaluate_summary_quality", return_value=bad_quality),
        ):
            compressed, result = engine.compress(messages)

        self.assertTrue(result.compressed)
        self.assertIn("【原文关键片段】", result.summary or "")
        # 降级内容进入实际输出（重构前缺陷：仅改 result.summary 元数据）
        summary_msgs = [m for m in compressed if m.get("role") == "system"]
        self.assertEqual(len(summary_msgs), 1)
        self.assertIn("【原文关键片段】", summary_msgs[0]["content"])


class ManagerStructuredContextTests(unittest.TestCase):
    """ContextManager.build_structured_context（/compact 消费路径）。"""

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


if __name__ == "__main__":
    unittest.main()
