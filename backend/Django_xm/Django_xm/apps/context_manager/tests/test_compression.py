"""上下文压缩引擎单元测试。

覆盖 Django_xm.apps.context_manager.services.compression：
- TokenEstimator：estimate_tokens 返回类型与非负性（空串为 0、中英文非空为正）、
  get_model_limit 已知模型命中与未知/空模型 fallback
- CompressionConfig.trigger_threshold 阈值计算
- ContextCompressionEngine.should_compress：超限触发 / 未超限不触发 /
  超限但可压缩短消息不足时不触发
- ContextCompressionEngine.compress：SUMMARY / SLIDING_WINDOW / HYBRID 策略选择、
  LLM 摘要调用（mock 引擎内部 _get_compression_model，不发起真实请求）、
  摘要超长截断、LLM 失败回退本地摘要
- create_compression_engine 工厂：模型上限映射与参数透传

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
    create_compression_engine,
)


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

    def test_compress_sliding_window_strategy_skips_llm(self) -> None:
        """SLIDING_WINDOW 策略：纯滑窗，不调用 LLM。"""
        config = _small_config(strategy=CompressionStrategy.SLIDING_WINDOW)
        engine = ContextCompressionEngine(config)
        llm_getter = mock.MagicMock()

        with mock.patch.object(engine, "_get_compression_model", llm_getter):
            compressed, result = engine.compress(_big_messages(4))

        llm_getter.assert_not_called()
        self.assertTrue(result.compressed)
        self.assertIs(result.strategy_used, CompressionStrategy.SLIDING_WINDOW)
        # 4 条消息 → 仅保留最近 keep_recent_messages=2 条
        self.assertEqual(len(compressed), 2)

    def test_compress_hybrid_is_default_strategy(self) -> None:
        """默认策略 HYBRID：摘要 + 滑窗混合，strategy_used=HYBRID。"""
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


class CreateCompressionEngineTests(unittest.TestCase):
    """create_compression_engine 工厂：模型上限映射与参数透传。"""

    def test_known_model_limit_mapped_to_config(self) -> None:
        """已知模型：max_context_tokens 取模型上限，阈值与策略按参数计算。"""
        engine = create_compression_engine("gpt-4")

        self.assertEqual(engine.config.max_context_tokens, 8192)
        self.assertEqual(engine.config.trigger_threshold, int(8192 * 0.8))
        self.assertIs(engine.config.strategy, CompressionStrategy.HYBRID)

    def test_unknown_model_and_custom_params_passthrough(self) -> None:
        """未知模型回退 128000；strategy/threshold_ratio/keep_recent 透传到配置。"""
        engine = create_compression_engine(
            "unknown-model-x", strategy="summary", threshold_ratio=0.5, keep_recent=3
        )

        self.assertEqual(engine.config.max_context_tokens, 128000)
        self.assertEqual(engine.config.trigger_threshold, 64000)
        self.assertIs(engine.config.strategy, CompressionStrategy.SUMMARY)
        self.assertEqual(engine.config.keep_recent_messages, 3)

    def test_no_model_name_falls_back_to_default_limit(self) -> None:
        """未指定模型名：回退默认上限 128000。"""
        engine = create_compression_engine()
        self.assertEqual(engine.config.max_context_tokens, 128000)


if __name__ == "__main__":
    unittest.main()
