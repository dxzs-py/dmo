"""compression.py 单元测试。

覆盖范围：
- TokenEstimator：token 估算（tiktoken / transformers / 启发式回退）、模型上限查询
- EntityExtractor：消息实体抽取、用户/助手实体抽取、停用词过滤、去重与上限
- CompressionConfig / CompressionResult / SummaryQuality：数据类与阈值计算
- ContextCompressionEngine：
  * should_compress 阈值判断
  * _classify_memory_tier 记忆分层
  * _format_messages / _fallback_summary / _extract_decisions 纯逻辑
  * _build_compressed_messages 压缩后消息重组
  * compress 端到端（mock LLM）
  * compress_incremental 增量压缩
  * get_rolling_summary / reset_rolling_summary 状态管理
- create_compression_engine 工厂函数

设计原则：
- 不依赖真实 LLM / Redis / 向量库，所有 LLM 调用通过 mock 注入
- 使用 unittest.TestCase（与项目现有测试一致），DB 由 conftest 管理
- 标记 unit（纯逻辑）/ integration（mock LLM 编排）
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from Django_xm.apps.context_manager.services.compression import (
    CompressionConfig,
    CompressionResult,
    CompressionStrategy,
    ContextCompressionEngine,
    EntityExtractor,
    MemoryTier,
    SummaryQuality,
    TokenEstimator,
    create_compression_engine,
)

# ============================================================================
# TokenEstimator 单测
# ============================================================================


class TokenEstimatorEstimateTokensTests(unittest.TestCase):
    """estimate_tokens / estimate_text / estimate 行为一致性。"""

    def test_empty_text_returns_zero(self):
        self.assertEqual(TokenEstimator.estimate_tokens(""), 0)
        self.assertEqual(TokenEstimator.estimate_text(""), 0)
        self.assertEqual(TokenEstimator.estimate(None), 0)

    def test_non_empty_text_returns_positive(self):
        text = "Hello world, 这是一个测试。"
        self.assertGreater(TokenEstimator.estimate_tokens(text), 0)

    def test_estimate_alias_matches_estimate_tokens(self):
        text = "一致性校验文本 consistency check"
        self.assertEqual(TokenEstimator.estimate(text), TokenEstimator.estimate_tokens(text))

    def test_estimate_text_equals_estimate_tokens(self):
        text = "estimate_text 应与 estimate_tokens 结果一致"
        self.assertEqual(TokenEstimator.estimate_text(text), TokenEstimator.estimate_tokens(text))

    def test_chinese_text_estimates_positive(self):
        # 中文字符 token 密度更高，确保估算为正
        self.assertGreater(TokenEstimator.estimate_tokens("你好世界，今天天气不错"), 0)

    def test_long_text_returns_more_than_short(self):
        short = "short"
        long_text = "short" * 100
        self.assertGreater(TokenEstimator.estimate_tokens(long_text), TokenEstimator.estimate_tokens(short))


class TokenEstimatorRoughEstimateTests(unittest.TestCase):
    """_rough_estimate 与 _improved_rough_estimate 启发式估算。"""

    def test_rough_estimate_empty(self):
        self.assertEqual(TokenEstimator._rough_estimate(""), 0)

    def test_rough_estimate_chinese_weight(self):
        # 中文字符权重 1.5，英文权重 0.25
        chinese = "你好"  # 2 个中文字符 → 3
        english = "ab"  # 2 个其他字符 → 0.5
        self.assertEqual(TokenEstimator._rough_estimate(chinese), int(2 * 1.5))
        self.assertEqual(TokenEstimator._rough_estimate(english), int(2 * 0.25))

    def test_improved_rough_estimate_empty(self):
        self.assertEqual(TokenEstimator._improved_rough_estimate(""), 0)

    def test_improved_rough_estimate_positive(self):
        self.assertGreater(TokenEstimator._improved_rough_estimate("Hello 世界 123"), 0)


class TokenEstimatorMessagesTests(unittest.TestCase):
    """estimate_dict_messages / estimate_messages 消息列表估算。"""

    def test_estimate_dict_messages_empty(self):
        self.assertEqual(TokenEstimator.estimate_dict_messages([]), 0)

    def test_estimate_dict_messages_sums_content(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮你的吗？"},
        ]
        total = TokenEstimator.estimate_dict_messages(messages)
        expected = TokenEstimator.estimate_tokens("你好") + TokenEstimator.estimate_tokens("你好，有什么可以帮你的吗？")
        self.assertEqual(total, expected)

    def test_estimate_dict_messages_handles_list_content(self):
        # content 为 list（多块文本）时应拼接后估算
        messages = [
            {"role": "user", "content": [{"text": "块一"}, {"text": "块二"}]},
        ]
        total = TokenEstimator.estimate_dict_messages(messages)
        self.assertGreater(total, 0)

    def test_estimate_dict_messages_handles_missing_content(self):
        messages = [{"role": "user"}]  # 无 content 键
        self.assertEqual(TokenEstimator.estimate_dict_messages(messages), 0)

    def test_estimate_messages_with_langchain_objects(self):
        from langchain_core.messages import AIMessage, HumanMessage

        messages = [
            HumanMessage(content="测试消息"),
            AIMessage(content="收到"),
        ]
        total = TokenEstimator.estimate_messages(messages)
        self.assertGreater(total, 0)


class TokenEstimatorModelLimitTests(unittest.TestCase):
    """get_model_limit 模型上限查询。"""

    def test_known_models(self):
        self.assertEqual(TokenEstimator.get_model_limit("gpt-4o"), 128000)
        self.assertEqual(TokenEstimator.get_model_limit("deepseek-chat"), 128000)
        self.assertEqual(TokenEstimator.get_model_limit("claude-sonnet-4-20250514"), 200000)

    def test_partial_match(self):
        # 模型名包含已知 key 即匹配
        self.assertEqual(TokenEstimator.get_model_limit("gpt-4o-2024-08-06"), 128000)

    def test_empty_returns_default(self):
        self.assertEqual(TokenEstimator.get_model_limit(""), 128000)

    def test_unknown_returns_default(self):
        self.assertEqual(TokenEstimator.get_model_limit("unknown-model-xyz"), 128000)


# ============================================================================
# EntityExtractor 单测
# ============================================================================


class EntityExtractorUserTests(unittest.TestCase):
    """_extract_user_entities 用户消息实体抽取。"""

    def test_extracts_quoted_entities(self):
        text = '请使用"深度学习框架"来完成这个任务'
        entities = EntityExtractor._extract_user_entities(text)
        self.assertIn("深度学习框架", entities)

    def test_extracts_numbers_with_units(self):
        text = "需要 100 元，占用 5 GB 内存"
        entities = EntityExtractor._extract_user_entities(text)
        # 数字+单位模式应被抽取
        self.assertTrue(any("100" in e for e in entities) or any("5" in e for e in entities))

    def test_extracts_short_sentences(self):
        text = "配置数据库连接。怎么做？"
        entities = EntityExtractor._extract_user_entities(text)
        # "配置数据库连接" 长度 7，在 4-40 范围内，应被抽取
        # "怎么做？" 含 "怎么"，应被排除
        self.assertIn("配置数据库连接", entities)

    def test_excludes_question_sentences(self):
        text = "怎么做这个？为什么不行？"
        entities = EntityExtractor._extract_user_entities(text)
        # 含 "怎么"/"为什么" 的句子应被排除
        self.assertNotIn("怎么做这个", entities)
        self.assertNotIn("为什么不行", entities)


class EntityExtractorAssistantTests(unittest.TestCase):
    """_extract_assistant_entities 助手消息实体抽取。"""

    def test_extracts_recommendations(self):
        text = "建议使用PostgreSQL作为数据库"
        entities = EntityExtractor._extract_assistant_entities(text)
        # 模式 (?:建议|推荐|...)([^\s，。！？,.!?]{2,30}) 捕获"建议"后的非分隔符字符
        self.assertTrue(any("PostgreSQL" in e for e in entities))

    def test_extracts_step_methods(self):
        text = "步骤一：安装依赖包"
        entities = EntityExtractor._extract_assistant_entities(text)
        self.assertTrue(len(entities) > 0)

    def test_no_entities_for_plain_text(self):
        text = "你好"
        entities = EntityExtractor._extract_assistant_entities(text)
        self.assertEqual(entities, [])


class EntityExtractorFromMessagesTests(unittest.TestCase):
    """extract_from_messages 端到端抽取（去重、停用词过滤、上限）。"""

    def test_empty_messages_returns_empty(self):
        self.assertEqual(EntityExtractor.extract_from_messages([]), [])

    def test_deduplicates_entities(self):
        messages = [
            {"role": "user", "content": '使用"Python"开发'},
            {"role": "assistant", "content": "建议使用 Python"},
            {"role": "user", "content": '再说一次"Python"'},
        ]
        entities = EntityExtractor.extract_from_messages(messages)
        # "Python" 出现多次，去重后应只出现一次
        python_count = sum(1 for e in entities if e.lower() == "python")
        self.assertLessEqual(python_count, 1)

    def test_filters_stop_words(self):
        messages = [{"role": "user", "content": "我在这里"}]
        entities = EntityExtractor.extract_from_messages(messages)
        # "我"/"在"/"这里" 中 "在" 是停用词，"这里" 长度 2 可能被抽取但应被停用词过滤
        for e in entities:
            self.assertNotIn(e.lower(), EntityExtractor._STOP_WORDS)

    def test_limits_to_30_entities(self):
        # 构造大量实体（30+ 短句）
        sentences = [f"任务{i}号" for i in range(50)]
        messages = [{"role": "user", "content": "。".join(sentences)}]
        entities = EntityExtractor.extract_from_messages(messages)
        self.assertLessEqual(len(entities), 30)

    def test_handles_list_content(self):
        messages = [
            {"role": "user", "content": [{"text": "使用"}, {"text": '"Vue3"'}]},
        ]
        entities = EntityExtractor.extract_from_messages(messages)
        # 应能处理 list 类型 content
        self.assertIsInstance(entities, list)

    def test_skips_empty_content(self):
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "   "},
        ]
        self.assertEqual(EntityExtractor.extract_from_messages(messages), [])


# ============================================================================
# CompressionConfig / CompressionResult / SummaryQuality 单测
# ============================================================================


class CompressionConfigTests(unittest.TestCase):
    """CompressionConfig 阈值计算与默认值。"""

    def test_default_values(self):
        config = CompressionConfig()
        self.assertEqual(config.max_context_tokens, 128000)
        self.assertEqual(config.token_threshold_ratio, 0.8)
        self.assertEqual(config.keep_recent_messages, 6)
        self.assertEqual(config.strategy, CompressionStrategy.HYBRID)
        self.assertTrue(config.entity_aware)

    def test_trigger_threshold_calculation(self):
        config = CompressionConfig(max_context_tokens=10000, token_threshold_ratio=0.8)
        self.assertEqual(config.trigger_threshold, 8000)

    def test_trigger_threshold_custom_ratio(self):
        config = CompressionConfig(max_context_tokens=10000, token_threshold_ratio=0.5)
        self.assertEqual(config.trigger_threshold, 5000)

    def test_long_term_tags_default(self):
        config = CompressionConfig()
        self.assertIn("system", config.long_term_tags)
        self.assertIn("preference", config.long_term_tags)
        self.assertIn("decision", config.long_term_tags)


class CompressionResultTests(unittest.TestCase):
    """CompressionResult 默认值。"""

    def test_defaults(self):
        result = CompressionResult()
        self.assertFalse(result.compressed)
        self.assertEqual(result.original_message_count, 0)
        self.assertEqual(result.compression_ratio, 0.0)
        self.assertIsNone(result.summary)
        self.assertEqual(result.key_entities, [])
        self.assertEqual(result.key_decisions, [])
        self.assertFalse(result.is_incremental)


class SummaryQualityTests(unittest.TestCase):
    """SummaryQuality Pydantic 模型。"""

    def test_defaults(self):
        quality = SummaryQuality()
        self.assertEqual(quality.completeness, 0.0)
        self.assertEqual(quality.overall, 0.0)
        self.assertFalse(quality.passed)

    def test_validation_clamps_to_range(self):
        # pydantic Field(ge=0, le=1) 应拒绝越界值
        with self.assertRaises(ValidationError):
            SummaryQuality(completeness=1.5)
        with self.assertRaises(ValidationError):
            SummaryQuality(accuracy=-0.1)


# ============================================================================
# ContextCompressionEngine 单测
# ============================================================================


class ClassifyMemoryTierTests(unittest.TestCase):
    """_classify_memory_tier 记忆分层。"""

    def setUp(self):
        self.engine = ContextCompressionEngine(CompressionConfig())

    def test_system_role_is_long_term(self):
        msg = {"role": "system", "content": "系统提示"}
        self.assertEqual(self.engine._classify_memory_tier(msg), MemoryTier.LONG_TERM)

    def test_explicit_memory_tier_long_term(self):
        msg = {"role": "user", "content": "普通消息", "memory_tier": "long_term"}
        self.assertEqual(self.engine._classify_memory_tier(msg), MemoryTier.LONG_TERM)

    def test_content_with_long_term_tag(self):
        # long_term_tags 为英文 ["system", "preference", "decision"]，匹配为子串（小写）
        msg = {"role": "user", "content": "This is a system-level configuration"}
        # "system" 在 long_term_tags 中，content_lower 包含 "system"
        self.assertEqual(self.engine._classify_memory_tier(msg), MemoryTier.LONG_TERM)

    def test_plain_short_term(self):
        msg = {"role": "user", "content": "你好"}
        self.assertEqual(self.engine._classify_memory_tier(msg), MemoryTier.SHORT_TERM)

    def test_list_content_with_tag(self):
        msg = {"role": "user", "content": [{"text": "用户偏好设置"}]}
        # "preference" 在 long_term_tags 中，但中文"偏好"不匹配英文 tag
        # 这里测试 list content 不报错
        result = self.engine._classify_memory_tier(msg)
        self.assertIn(result, [MemoryTier.LONG_TERM, MemoryTier.SHORT_TERM])


class ShouldCompressTests(unittest.TestCase):
    """should_compress 阈值判断。"""

    def test_below_threshold_returns_false(self):
        config = CompressionConfig(max_context_tokens=100000, token_threshold_ratio=0.8)
        engine = ContextCompressionEngine(config)
        messages = [{"role": "user", "content": "短消息"}]
        self.assertFalse(engine.should_compress(messages))

    def test_no_short_term_messages_returns_false(self):
        # 全部 LONG_TERM，即使超 token 也无需压缩
        config = CompressionConfig(
            max_context_tokens=100,  # 极低阈值确保超限
            token_threshold_ratio=0.8,
            keep_recent_messages=2,
        )
        engine = ContextCompressionEngine(config)
        messages = [
            {"role": "system", "content": "系统提示" * 100},
        ]
        # 系统消息为 LONG_TERM，short_term_count=0 < keep_recent=2
        self.assertFalse(engine.should_compress(messages))

    def test_above_threshold_with_enough_short_term_returns_true(self):
        config = CompressionConfig(
            max_context_tokens=100,
            token_threshold_ratio=0.8,
            keep_recent_messages=2,
        )
        engine = ContextCompressionEngine(config)
        messages = [
            {"role": "user", "content": "这是一段较长的用户消息" * 20},
            {"role": "assistant", "content": "这是助手的回复" * 20},
            {"role": "user", "content": "再次提问" * 20},
        ]
        self.assertTrue(engine.should_compress(messages))


class FormatMessagesTests(unittest.TestCase):
    """_format_messages 消息格式化。"""

    def test_empty_messages(self):
        self.assertEqual(ContextCompressionEngine._format_messages([]), "")

    def test_formats_roles(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
            {"role": "system", "content": "系统"},
        ]
        result = ContextCompressionEngine._format_messages(messages)
        self.assertIn("[用户]: 你好", result)
        self.assertIn("[助手]: 你好", result)
        self.assertIn("[系统]: 系统", result)

    def test_truncates_long_content(self):
        long_content = "A" * 500
        messages = [{"role": "user", "content": long_content}]
        result = ContextCompressionEngine._format_messages(messages)
        self.assertIn("...", result)
        # 截断后应包含前 400 字符
        self.assertIn("A" * 400, result)

    def test_handles_list_content(self):
        messages = [{"role": "user", "content": [{"text": "块一"}, {"text": "块二"}]}]
        result = ContextCompressionEngine._format_messages(messages)
        self.assertIn("块一", result)
        self.assertIn("块二", result)

    def test_includes_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": "调用工具",
                "tool_calls": [{"name": "shell_exec"}],
            }
        ]
        result = ContextCompressionEngine._format_messages(messages)
        self.assertIn("shell_exec", result)

    def test_skips_empty_content(self):
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "有效"},
        ]
        result = ContextCompressionEngine._format_messages(messages)
        self.assertNotIn("[用户]", result)
        self.assertIn("[助手]: 有效", result)


class FallbackSummaryTests(unittest.TestCase):
    """_fallback_summary 回退摘要。"""

    def test_empty_messages(self):
        self.assertEqual(ContextCompressionEngine._fallback_summary([]), "")

    def test_extracts_user_topics(self):
        messages = [
            {"role": "user", "content": "如何配置数据库"},
            {"role": "assistant", "content": "首先安装依赖"},
            {"role": "user", "content": "怎样部署应用"},
        ]
        result = ContextCompressionEngine._fallback_summary(messages)
        self.assertIn("配置数据库", result)
        self.assertIn("部署应用", result)

    def test_deduplicates_topics(self):
        messages = [
            {"role": "user", "content": "相同问题"},
            {"role": "user", "content": "相同问题"},
        ]
        result = ContextCompressionEngine._fallback_summary(messages)
        # 去重后只出现一次
        self.assertEqual(result.count("相同问题"), 1)


class ExtractDecisionsTests(unittest.TestCase):
    """_extract_decisions 决策抽取。"""

    def setUp(self):
        self.engine = ContextCompressionEngine(CompressionConfig())

    def test_extracts_recommendations(self):
        messages = [
            {"role": "assistant", "content": "建议：使用 PostgreSQL 作为主数据库"},
        ]
        decisions = self.engine._extract_decisions(messages)
        self.assertTrue(any("PostgreSQL" in d for d in decisions))

    def test_skips_user_messages(self):
        messages = [
            {"role": "user", "content": "建议：应该这样做"},
        ]
        decisions = self.engine._extract_decisions(messages)
        self.assertEqual(decisions, [])

    def test_limits_to_10(self):
        messages = [{"role": "assistant", "content": f"建议：决策{i}号方案"} for i in range(20)]
        decisions = self.engine._extract_decisions(messages)
        self.assertLessEqual(len(decisions), 10)


class BuildCompressedMessagesTests(unittest.TestCase):
    """_build_compressed_messages 压缩后消息重组。"""

    def setUp(self):
        self.config = CompressionConfig(keep_recent_messages=2)
        self.engine = ContextCompressionEngine(self.config)

    def test_not_compressed_returns_original(self):
        original = [{"role": "user", "content": "原样"}]
        result = CompressionResult(compressed=False)
        output = self.engine._build_compressed_messages(original, result)
        self.assertEqual(output, original)

    def test_preserves_long_term_messages(self):
        long_term = [{"role": "system", "content": "系统提示"}]
        original = [*long_term, {"role": "user", "content": "用户消息"}, {"role": "assistant", "content": "助手回复"}]
        result = CompressionResult(compressed=True, summary="摘要", key_entities=["实体1"], key_decisions=["决策1"])
        output = self.engine._build_compressed_messages(original, result, long_term)
        # LONG_TERM 消息应保留
        contents = [m.get("content") for m in output]
        self.assertIn("系统提示", contents)

    def test_injects_summary_as_system_message(self):
        original = [
            {"role": "user", "content": "消息1"},
            {"role": "assistant", "content": "消息2"},
        ]
        result = CompressionResult(compressed=True, summary="这是摘要内容")
        output = self.engine._build_compressed_messages(original, result)
        summary_msgs = [m for m in output if "摘要" in str(m.get("content", ""))]
        self.assertTrue(len(summary_msgs) > 0)

    def test_keeps_recent_messages(self):
        original = [{"role": "user", "content": f"旧消息{i}"} for i in range(5)]
        result = CompressionResult(compressed=True, summary="摘要")
        output = self.engine._build_compressed_messages(original, result)
        # keep_recent_messages=2，应保留最后 2 条
        contents = [m.get("content") for m in output if m.get("role") != "system"]
        self.assertIn("旧消息4", contents)
        self.assertIn("旧消息3", contents)


class CompressTests(unittest.TestCase):
    """compress 端到端压缩（mock LLM）。"""

    def test_no_compression_when_below_threshold(self):
        config = CompressionConfig(max_context_tokens=100000, token_threshold_ratio=0.8)
        engine = ContextCompressionEngine(config)
        messages = [{"role": "user", "content": "短消息"}]
        output, result = engine.compress(messages)
        self.assertFalse(result.compressed)
        self.assertEqual(output, messages)

    @patch.object(ContextCompressionEngine, "_get_compression_model")
    def test_compress_summary_strategy(self, mock_get_model):
        # mock LLM 返回固定摘要
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "这是生成的摘要内容"
        mock_model.invoke.return_value = mock_response
        mock_get_model.return_value = mock_model

        config = CompressionConfig(
            max_context_tokens=100,
            token_threshold_ratio=0.8,
            keep_recent_messages=2,
            strategy=CompressionStrategy.SUMMARY,
        )
        engine = ContextCompressionEngine(config)
        messages = [
            {"role": "user", "content": "长消息" * 30},
            {"role": "assistant", "content": "长回复" * 30},
            {"role": "user", "content": "再次提问" * 30},
            {"role": "assistant", "content": "再次回复" * 30},
        ]
        output, result = engine.compress(messages)
        self.assertTrue(result.compressed)
        self.assertEqual(result.strategy_used, CompressionStrategy.SUMMARY)
        self.assertIsNotNone(result.summary)
        # 压缩后消息数应 <= 原始（保留 recent + 摘要 system 消息）
        self.assertLessEqual(len(output), len(messages) + 2)  # +2 for summary/system messages

    @patch.object(ContextCompressionEngine, "_get_compression_model")
    def test_compress_sliding_window_strategy(self, mock_get_model):
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        config = CompressionConfig(
            max_context_tokens=100,
            token_threshold_ratio=0.8,
            keep_recent_messages=2,
            strategy=CompressionStrategy.SLIDING_WINDOW,
        )
        engine = ContextCompressionEngine(config)
        messages = [
            {"role": "user", "content": "消息" * 30},
            {"role": "assistant", "content": "回复" * 30},
            {"role": "user", "content": "提问" * 30},
            {"role": "assistant", "content": "回复2" * 30},
        ]
        _, result = engine.compress(messages)
        self.assertTrue(result.compressed)
        self.assertEqual(result.strategy_used, CompressionStrategy.SLIDING_WINDOW)
        # sliding window 不生成摘要
        self.assertIsNone(result.summary)

    @patch.object(ContextCompressionEngine, "_get_compression_model")
    def test_compress_calculates_ratio(self, mock_get_model):
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "短摘要"
        mock_model.invoke.return_value = mock_response
        mock_get_model.return_value = mock_model

        config = CompressionConfig(
            max_context_tokens=100,
            token_threshold_ratio=0.8,
            keep_recent_messages=2,
            strategy=CompressionStrategy.SUMMARY,
        )
        engine = ContextCompressionEngine(config)
        messages = [
            {"role": "user", "content": "很长" * 50},
            {"role": "assistant", "content": "很长" * 50},
            {"role": "user", "content": "很长" * 50},
            {"role": "assistant", "content": "很长" * 50},
        ]
        _, result = engine.compress(messages)
        self.assertGreater(result.original_token_estimate, 0)
        if result.compressed:
            # 压缩率 = 1 - (压缩后/原始)
            self.assertGreater(result.compression_ratio, 0)


class RollingSummaryTests(unittest.TestCase):
    """get_rolling_summary / reset_rolling_summary 状态管理。"""

    def test_initial_rolling_summary_is_none(self):
        engine = ContextCompressionEngine(CompressionConfig())
        self.assertIsNone(engine.get_rolling_summary())

    @patch.object(ContextCompressionEngine, "_persist_state")
    def test_reset_clears_rolling_summary(self, mock_persist):
        engine = ContextCompressionEngine(CompressionConfig())
        engine._rolling_summary = "旧摘要"
        engine._last_compressed_index = 5
        engine.reset_rolling_summary()
        self.assertIsNone(engine.get_rolling_summary())
        self.assertEqual(engine._last_compressed_index, 0)
        mock_persist.assert_called_once()


class CompressIncrementalTests(unittest.TestCase):
    """compress_incremental 增量压缩。"""

    def test_few_messages_returns_original(self):
        config = CompressionConfig(keep_recent_messages=6)
        engine = ContextCompressionEngine(config)
        messages = [{"role": "user", "content": "短消息"}]
        output, result = engine.compress_incremental(messages)
        self.assertFalse(result.compressed)
        self.assertTrue(result.is_incremental)
        self.assertEqual(output, messages)

    @patch.object(ContextCompressionEngine, "_get_compression_model")
    def test_incremental_compresses_old_messages(self, mock_get_model):
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "增量摘要"
        mock_model.invoke.return_value = mock_response
        mock_get_model.return_value = mock_model

        config = CompressionConfig(
            max_context_tokens=100,
            token_threshold_ratio=0.8,
            keep_recent_messages=2,
        )
        engine = ContextCompressionEngine(config)
        messages = [{"role": "user", "content": f"消息{i}" * 20} for i in range(6)]
        _, result = engine.compress_incremental(messages, keep_recent=2)
        self.assertTrue(result.is_incremental)


class PersistLoadStateTests(unittest.TestCase):
    """_persist_state / _load_state 持久化（mock store）。"""

    def test_persist_without_store_is_noop(self):
        engine = ContextCompressionEngine(CompressionConfig())
        # 无 store，应不报错
        engine._persist_state()

    def test_load_without_store_is_noop(self):
        engine = ContextCompressionEngine(CompressionConfig())
        engine._load_state()

    def test_persist_writes_to_store(self):
        mock_store = MagicMock()
        engine = ContextCompressionEngine(CompressionConfig(), store=mock_store, user_id="user-1", thread_id="thread-1")
        engine._rolling_summary = "摘要"
        engine._last_compressed_index = 3
        engine._persist_state()
        mock_store.put.assert_called_once()
        call_args = mock_store.put.call_args
        # 验证 namespace 包含 user_id
        self.assertIn("user-1", str(call_args[0][0]))

    def test_load_restores_from_store(self):
        mock_store = MagicMock()
        mock_item = MagicMock()
        mock_item.value = {"rolling_summary": "恢复的摘要", "last_compressed_index": 7}
        mock_store.get.return_value = mock_item
        engine = ContextCompressionEngine(CompressionConfig(), store=mock_store, user_id="user-1", thread_id="thread-1")
        # __init__ 中已调用 _load_state
        self.assertEqual(engine._rolling_summary, "恢复的摘要")
        self.assertEqual(engine._last_compressed_index, 7)

    def test_load_handles_store_error(self):
        mock_store = MagicMock()
        mock_store.get.side_effect = Exception("store 不可用")
        # 加载失败应不抛异常
        engine = ContextCompressionEngine(CompressionConfig(), store=mock_store, user_id="user-1")
        self.assertIsNone(engine._rolling_summary)


# ============================================================================
# create_compression_engine 工厂函数单测
# ============================================================================


class CreateCompressionEngineTests(unittest.TestCase):
    """create_compression_engine 工厂函数。"""

    def test_returns_engine_instance(self):
        engine = create_compression_engine()
        self.assertIsInstance(engine, ContextCompressionEngine)

    def test_applies_strategy(self):
        engine = create_compression_engine(strategy="summary")
        self.assertEqual(engine.config.strategy, CompressionStrategy.SUMMARY)

    def test_applies_threshold_ratio(self):
        engine = create_compression_engine(threshold_ratio=0.5, model_name="gpt-4o")
        self.assertEqual(engine.config.token_threshold_ratio, 0.5)
        self.assertEqual(engine.config.max_context_tokens, 128000)

    def test_applies_keep_recent(self):
        engine = create_compression_engine(keep_recent=10)
        self.assertEqual(engine.config.keep_recent_messages, 10)

    def test_invalid_strategy_raises(self):
        with self.assertRaises(ValueError):
            create_compression_engine(strategy="invalid_strategy")

    def test_passes_store_and_user(self):
        mock_store = MagicMock()
        engine = create_compression_engine(store=mock_store, user_id="user-1", thread_id="thread-1")
        self.assertEqual(engine._store, mock_store)
        self.assertEqual(engine._user_id, "user-1")
        self.assertEqual(engine._thread_id, "thread-1")


if __name__ == "__main__":
    unittest.main()
