"""上下文压缩引擎单元测试。

覆盖 Django_xm.apps.context_manager.services.compression：
- TokenEstimator：estimate_tokens 返回类型与非负性（空串为 0、中英文非空为正）、
  get_model_limit 已知模型命中与未知/空模型 fallback
- CompressionConfig.trigger_threshold 阈值计算
- ContextCompressionEngine.should_compress：超限触发 / 未超限不触发 /
  超限但可压缩 token 不足时不触发 / 少量超长消息按 token 触发（4.1）
- parse_compression_strategy：合法值解析、已删除策略（sliding_window）报错；
  ContextManager 加载存量 sliding_window 配置回退 HYBRID（4.2）
- ContextCompressionEngine.compress：SUMMARY / HYBRID 策略选择、
  LLM 摘要调用（mock 引擎内部 _get_compression_model，不发起真实请求）、
  摘要超长截断、LLM 失败回退本地摘要

mock 点说明：LLM 调用发生在 _generate_summary → _get_compression_model() →
model.invoke(...)，测试在引擎实例上 patch _get_compression_model 返回假模型；
compress 的摘要质量评估 evaluate_summary_quality 会触发 llm_factory 导入，
同样在实例上 patch 为固定评分，保证测试不依赖 DB / LLM。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.context_manager.tests.test_compression
（纯单元测试，无 DB / Redis / LLM 依赖）
"""

import os
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.context_manager.services.compression import (
    CompressionConfig,
    CompressionStrategy,
    ContextCompressionEngine,
    SummaryQuality,
    TokenEstimator,
    parse_compression_strategy,
)
from Django_xm.apps.context_manager.services.manager import ContextManagementConfig, ContextManager


def _small_config(**overrides: Any) -> CompressionConfig:
    """构造小阈值压缩配置（trigger_threshold = int(100 * 0.8) = 80 token）。"""
    defaults: dict[str, Any] = {
        "max_context_tokens": 100,
        "token_threshold_ratio": 0.8,
        "keep_recent_messages": 2,
        "summary_max_length": 50,
    }
    defaults.update(overrides)
    return CompressionConfig(**defaults)


def _big_messages(count: int = 4) -> list[dict[str, Any]]:
    """构造超阈值的短消息列表（每条约 90+ token，共 4 条）。"""
    return [{"role": "user", "content": "hello world " * 30} for _ in range(count)]


def _fake_llm(content: str) -> mock.MagicMock:
    """构造返回固定 content 的假 LLM 模型。"""
    response = mock.MagicMock()
    response.content = content
    model = mock.MagicMock()
    model.invoke.return_value = response
    return model


class TokenEstimatorTests(unittest.TestCase):
    """TokenEstimator：token 估算与模型上限查询。"""

    def test_estimate_tokens_returns_non_negative_int(self) -> None:
        """中英文/空串估算均返回非负 int。"""
        for text in ["", "hello world", "你好世界，这是一段中文测试文本", "mixed 中英 mixed 文本 123"]:
            with self.subTest(text=text[:20]):
                value = TokenEstimator.estimate_tokens(text)
                self.assertIsInstance(value, int)
                self.assertGreaterEqual(value, 0)

    def test_estimate_tokens_empty_is_zero_and_non_empty_positive(self) -> None:
        """空串恒为 0；非空文本（中/英）估算为正。"""
        self.assertEqual(TokenEstimator.estimate_tokens(""), 0)
        self.assertGreater(TokenEstimator.estimate_tokens("你好世界测试文本"), 0)
        self.assertGreater(TokenEstimator.estimate_tokens("hello world example"), 0)

    def test_get_model_limit_known_models(self) -> None:
        """已知模型命中 _MODEL_LIMITS（子串匹配）。"""
        cases = {
            "gpt-4o": 128000,
            "gpt-4": 8192,
            "gpt-3.5-turbo": 16385,
            "claude-sonnet-4-20250514": 200000,
            "deepseek-chat": 128000,
        }
        for model_name, limit in cases.items():
            with self.subTest(model=model_name):
                self.assertEqual(TokenEstimator.get_model_limit(model_name), limit)

    def test_get_model_limit_unknown_and_empty_fallback(self) -> None:
        """未知模型与空模型名回退到 128000 默认上限。"""
        self.assertEqual(TokenEstimator.get_model_limit("totally-unknown-model"), 128000)
        self.assertEqual(TokenEstimator.get_model_limit(""), 128000)

    def test_get_model_limit_gemini_models(self) -> None:
        """gemini 系列命中 _MODEL_LIMITS 新增条目。"""
        cases = {
            "gemini-2.0-flash-exp": 1000000,
            "gemini-pro": 32768,
        }
        for model_name, limit in cases.items():
            with self.subTest(model=model_name):
                self.assertEqual(TokenEstimator.get_model_limit(model_name), limit)


class CompressionConfigTests(unittest.TestCase):
    """CompressionConfig：触发阈值计算。"""

    def test_trigger_threshold_property(self) -> None:
        """trigger_threshold = int(max_context_tokens * token_threshold_ratio)。"""
        config = CompressionConfig(max_context_tokens=1000, token_threshold_ratio=0.8)
        self.assertEqual(config.trigger_threshold, 800)
        self.assertEqual(_small_config().trigger_threshold, 80)


class ShouldCompressTests(unittest.TestCase):
    """ContextCompressionEngine.should_compress：压缩触发阈值判断。"""

    def test_below_threshold_returns_false(self) -> None:
        """未超限：不触发压缩。"""
        engine = ContextCompressionEngine(_small_config())
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there"},
        ]
        self.assertFalse(engine.should_compress(messages))

    def test_above_threshold_with_enough_short_term_returns_true(self) -> None:
        """超限且可压缩短消息数足够：触发压缩。"""
        engine = ContextCompressionEngine(_small_config())
        self.assertTrue(engine.should_compress(_big_messages(4)))

    def test_above_threshold_but_insufficient_short_term_returns_false(self) -> None:
        """超限但 SHORT_TERM 消息不足 keep_recent_messages：不触发（无可压缩空间）。"""
        engine = ContextCompressionEngine(_small_config(keep_recent_messages=2))
        messages = [
            {"role": "user", "content": "long text " * 100, "memory_tier": "long_term"}
            for _ in range(4)
        ] + [{"role": "user", "content": "hi"}]
        self.assertFalse(engine.should_compress(messages))

    def test_above_threshold_few_oversized_short_term_triggers(self) -> None:
        """4.1：3 条超长消息（各超 2k token）不足保底窗口（keep_recent=6）仍触发压缩。

        旧判定 ``short_term_count >= keep_recent`` 下该场景永不压缩（死锁）；
        新增 token 触发条件：可压缩部分 token 总量超阈值即触发。
        """
        # 阈值 = int(4096 * 0.8) = 3276 token；3 条中文长消息各 3k+ token，总量约 9k+
        config = _small_config(max_context_tokens=4096, keep_recent_messages=6)
        engine = ContextCompressionEngine(config)
        messages = [{"role": "user", "content": "长" * 3000} for _ in range(3)]

        self.assertGreater(
            TokenEstimator.estimate_dict_messages(messages), config.trigger_threshold
        )
        self.assertTrue(engine.should_compress(messages))

    def test_above_threshold_few_tiny_short_term_not_triggered(self) -> None:
        """4.1 反向：SHORT_TERM 条数不足且可压缩部分 token 未超阈值 → 不触发。

        防止少量长_term 消息撑爆总量、剩余极少 SHORT_TERM 内容被误压缩。
        """
        engine = ContextCompressionEngine(_small_config(max_context_tokens=4096, keep_recent_messages=6))
        messages = [
            {"role": "user", "content": "长" * 3000, "memory_tier": "long_term"}
            for _ in range(3)
        ] + [{"role": "user", "content": "hi"}]
        # 总量超阈值（长_term 消息贡献），但可压缩部分仅 "hi" ≈ 1 token
        self.assertGreater(TokenEstimator.estimate_dict_messages(messages), 4096 * 0.8)
        self.assertFalse(engine.should_compress(messages))

    def test_compress_few_oversized_messages_produces_summary(self) -> None:
        """4.1 端到端：3 条超长消息 compress 产出非空摘要 + 原文保留。"""
        config = _small_config(max_context_tokens=4096, keep_recent_messages=6)
        engine = ContextCompressionEngine(config)
        fake_model = _fake_llm("三条超长消息的摘要")
        messages = [{"role": "user", "content": "长" * 3000} for _ in range(3)]

        with (
            mock.patch.object(engine, "_get_compression_model", return_value=fake_model),
            mock.patch.object(
                engine,
                "evaluate_summary_quality",
                return_value=SummaryQuality(
                    completeness=0.9, accuracy=0.9, conciseness=0.9, overall=0.9, passed=True
                ),
            ),
        ):
            compressed, result = engine.compress(messages)

        self.assertTrue(result.compressed)
        self.assertTrue(result.summary)
        # 输出保留原始 3 条消息（保底窗口覆盖全部）+ 1 条摘要 system 消息
        self.assertEqual(len(compressed), 4)
        summary_messages = [m for m in compressed if m["role"] == "system"]
        self.assertEqual(len(summary_messages), 1)
        self.assertIn("三条超长消息的摘要", summary_messages[0]["content"])
        self.assertEqual(sum(1 for m in compressed if m["role"] == "user"), 3)


class CompressTests(unittest.TestCase):
    """ContextCompressionEngine.compress：策略选择与 LLM 摘要调用隔离。"""

    def test_compress_without_trigger_returns_original(self) -> None:
        """未达阈值：原样返回消息，compressed=False，token 估算不变。"""
        engine = ContextCompressionEngine(_small_config())
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        compressed, result = engine.compress(messages)

        self.assertIs(compressed, messages)
        self.assertFalse(result.compressed)
        self.assertEqual(result.original_message_count, 2)
        self.assertEqual(result.original_token_estimate, result.compressed_token_estimate)
        self.assertIsNone(result.strategy_used)

    def test_compress_summary_strategy_calls_llm_and_truncates(self) -> None:
        """SUMMARY 策略：调用 LLM 生成摘要并按 summary_max_length 截断。"""
        config = _small_config(strategy=CompressionStrategy.SUMMARY)
        engine = ContextCompressionEngine(config)
        fake_model = _fake_llm("S" * 200)

        with (
            mock.patch.object(engine, "_get_compression_model", return_value=fake_model),
            mock.patch.object(
                engine,
                "evaluate_summary_quality",
                return_value=SummaryQuality(
                    completeness=0.9, accuracy=0.9, conciseness=0.9, overall=0.9, passed=True
                ),
            ),
        ):
            compressed, result = engine.compress(_big_messages(4))

        self.assertTrue(result.compressed)
        self.assertIs(result.strategy_used, CompressionStrategy.SUMMARY)
        self.assertEqual(result.summary, "S" * 50)
        fake_model.invoke.assert_called_once()

        # 压缩输出：1 条摘要 system 消息 + 最近 keep_recent_messages 条
        self.assertEqual(len(compressed), 3)
        summary_messages = [m for m in compressed if m["role"] == "system"]
        self.assertEqual(len(summary_messages), 1)
        self.assertIn("【对话摘要】", summary_messages[0]["content"])

    def test_compress_hybrid_is_default_strategy(self) -> None:
        """默认策略 HYBRID：摘要 + 实体/决策抽取混合，strategy_used=HYBRID。"""
        engine = ContextCompressionEngine(_small_config(summary_max_length=200))
        fake_model = _fake_llm("混合摘要内容")

        with (
            mock.patch.object(engine, "_get_compression_model", return_value=fake_model),
            mock.patch.object(
                engine,
                "evaluate_summary_quality",
                return_value=SummaryQuality(
                    completeness=0.9, accuracy=0.9, conciseness=0.9, overall=0.9, passed=True
                ),
            ),
        ):
            _, result = engine.compress(_big_messages(4))

        self.assertTrue(result.compressed)
        self.assertIs(result.strategy_used, CompressionStrategy.HYBRID)
        self.assertEqual(result.summary, "混合摘要内容")

    def test_generate_summary_llm_failure_falls_back_to_local_summary(self) -> None:
        """LLM 不可用/异常：回退本地摘要（"对话摘要：讨论了 …"），不抛异常。"""
        engine = ContextCompressionEngine(_small_config(summary_max_length=200))
        messages = [{"role": "user", "content": "如何部署服务到生产环境"}]

        with mock.patch.object(
            engine, "_get_compression_model", side_effect=RuntimeError("llm down")
        ):
            summary = engine._generate_summary(messages, [])

        self.assertTrue(summary.startswith("对话摘要"))
        self.assertIn("如何部署服务到生产环境", summary)


class CompressionStrategyConfigTests(unittest.TestCase):
    """压缩策略配置解析与存量配置回退（fix-review-round2 4.2）。"""

    def test_parse_valid_strategies(self) -> None:
        """合法值 summary/hybrid 正常解析。"""
        self.assertIs(parse_compression_strategy("summary"), CompressionStrategy.SUMMARY)
        self.assertIs(parse_compression_strategy("hybrid"), CompressionStrategy.HYBRID)

    def test_parse_removed_sliding_window_raises(self) -> None:
        """已删除策略 sliding_window：抛出携带迁移说明的明确 ValueError。"""
        with self.assertRaises(ValueError) as ctx:
            parse_compression_strategy("sliding_window")
        self.assertIn("sliding_window", str(ctx.exception))
        self.assertIn("已删除", str(ctx.exception))

    def test_parse_unknown_value_raises(self) -> None:
        """未知策略值同样报 ValueError。"""
        with self.assertRaises(ValueError):
            parse_compression_strategy("nonexistent")

    def test_manager_loads_removed_strategy_falls_back_to_hybrid(self) -> None:
        """存量配置 compression_strategy='sliding_window'：加载报错并回退 HYBRID。"""
        config = ContextManagementConfig(
            compression_enabled=True,
            compression_strategy="sliding_window",
            knowledge_graph_enabled=False,
            cross_session_enabled=False,
        )
        with self.assertLogs(
            "Django_xm.apps.context_manager.services.manager", level="ERROR"
        ) as captured:
            manager = ContextManager(config=config)
        self.assertIn("sliding_window", "\n".join(captured.output))
        self.assertIsNotNone(manager._compression_engine)
        self.assertIs(manager._compression_engine.config.strategy, CompressionStrategy.HYBRID)

    def test_manager_loads_valid_strategy(self) -> None:
        """合法配置正常加载对应策略（回归保护）。"""
        config = ContextManagementConfig(
            compression_enabled=True,
            compression_strategy="summary",
            knowledge_graph_enabled=False,
            cross_session_enabled=False,
        )
        manager = ContextManager(config=config)
        self.assertIs(manager._compression_engine.config.strategy, CompressionStrategy.SUMMARY)


if __name__ == "__main__":
    unittest.main()
