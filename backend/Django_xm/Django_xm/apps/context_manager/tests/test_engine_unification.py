"""ARCH-07 双压缩引擎合并后的统一性测试。

验证合并方案：
- 单一引擎 ContextCompressionEngine 支持 dict / BaseMessage 双语义输入（适配层）；
- ProgressiveCompressor 薄壳化：分级阈值配置单源（context_settings），
  Level 4 激进压缩委托引擎 compress_aggressive；
- load_research_context 摘要旁路改调统一引擎 summarize_long_text。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.context_manager.tests.test_engine_unification
"""

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.compression import (
    CompressionConfig,
    ContextCompressionEngine,
    dicts_to_messages,
    messages_to_dicts,
)
from Django_xm.apps.context_manager.services.progressive_compressor import (
    CompressionLevel,
    ProgressiveCompressor,
)

_BIG_CONTENT = "hello world " * 30


def _fake_llm(content: str):
    response = mock.MagicMock()
    response.content = content
    model = mock.MagicMock()
    model.invoke.return_value = response
    return model


def _small_engine() -> tuple[ContextCompressionEngine, mock.MagicMock]:
    """小阈值统一引擎 + 假 LLM（trigger = 100 * 0.8 = 80 token）。"""
    engine = ContextCompressionEngine(
        CompressionConfig(
            max_context_tokens=100,
            token_threshold_ratio=0.8,
            keep_recent_messages=2,
            summary_max_length=50,
        )
    )
    return engine, _fake_llm("统一摘要")


class AdapterRoundTripTests(unittest.TestCase):
    """dict / BaseMessage 适配层往返。"""

    def test_messages_to_dicts_preserves_fields(self) -> None:
        msg = HumanMessage(content="问题", id="m1")
        msg.additional_kwargs["memory_tier"] = "long_term"
        dicts = messages_to_dicts([msg])
        self.assertEqual(dicts[0]["role"], "user")
        self.assertEqual(dicts[0]["content"], "问题")
        self.assertEqual(dicts[0]["id"], "m1")
        self.assertEqual(dicts[0]["memory_tier"], "long_term")

    def test_dicts_round_trip_restores_typed_messages(self) -> None:
        dicts = [
            {"role": "system", "content": "系统提示", "memory_tier": "long_term", "id": "s1"},
            {"role": "user", "content": "问题", "id": "u1"},
            {"role": "assistant", "content": "回答", "id": "a1"},
        ]
        messages = dicts_to_messages(dicts)
        self.assertTrue(all(isinstance(m, BaseMessage) for m in messages))
        self.assertEqual([m.id for m in messages], ["s1", "u1", "a1"])
        self.assertEqual(messages[0].type, "system")
        self.assertEqual(messages[0].additional_kwargs.get("memory_tier"), "long_term")
        # 往返一致性：再转回 dict 内容不变
        again = messages_to_dicts(messages)
        self.assertEqual([d["content"] for d in again], ["系统提示", "问题", "回答"])


class CompressMessagesDualSemanticsTests(unittest.TestCase):
    """统一入口 compress_messages：双语义自动识别。"""

    def test_base_message_input_returns_base_messages(self) -> None:
        engine, fake_model = _small_engine()
        with mock.patch.object(engine, "_get_compression_model", return_value=fake_model):
            compressed, result = engine.compress_messages(
                [HumanMessage(content=_BIG_CONTENT) for _ in range(4)]
            )
        self.assertTrue(result.compressed)
        self.assertTrue(all(isinstance(m, BaseMessage) for m in compressed))
        # 1 条摘要 + 最近 2 条
        self.assertEqual(len(compressed), 3)

    def test_dict_input_returns_dicts(self) -> None:
        engine, fake_model = _small_engine()
        with mock.patch.object(engine, "_get_compression_model", return_value=fake_model):
            compressed, result = engine.compress_messages(
                [{"role": "user", "content": _BIG_CONTENT} for _ in range(4)]
            )
        self.assertTrue(result.compressed)
        self.assertTrue(all(isinstance(m, dict) for m in compressed))
        self.assertEqual(len(compressed), 3)


class SummarizeLongTextTests(unittest.TestCase):
    """统一摘要能力 summarize_long_text（load_research_context 旁路复用）。"""

    def test_returns_model_summary(self) -> None:
        engine = ContextCompressionEngine()
        fake_model = _fake_llm("  引擎生成的摘要  ")
        with mock.patch.object(engine, "_get_compression_model", return_value=fake_model):
            summary = engine.summarize_long_text("很长的文本", "请总结：")
        self.assertEqual(summary, "引擎生成的摘要")
        # prompt = instruction + 换行 + 原文
        sent = fake_model.invoke.call_args[0][0]
        self.assertEqual(sent[0]["role"], "user")
        self.assertIn("请总结：", sent[0]["content"])
        self.assertIn("很长的文本", sent[0]["content"])

    def test_empty_text_returns_empty(self) -> None:
        engine = ContextCompressionEngine()
        self.assertEqual(engine.summarize_long_text("   ", "请总结："), "")

    def test_exception_propagates(self) -> None:
        """异常向上抛出（由调用方决定降级，保持原旁路 None 语义）。"""
        engine = ContextCompressionEngine()
        with mock.patch.object(engine, "_get_compression_model", side_effect=RuntimeError("llm down")):
            with self.assertRaises(RuntimeError):
                engine.summarize_long_text("文本", "请总结：")


class ConfigSingleSourceTests(unittest.TestCase):
    """配置单源（ARCH-07）：薄壳分级阈值唯一来源 context_settings。"""

    def test_default_thresholds_from_settings(self) -> None:
        thresholds = ProgressiveCompressor._DEFAULT_THRESHOLDS
        self.assertEqual(thresholds[CompressionLevel.LEVEL_2_SUMMARY], context_settings.compression_level_2_threshold)
        self.assertEqual(thresholds[CompressionLevel.LEVEL_3_RELEVANCE], context_settings.compression_level_3_threshold)
        self.assertEqual(thresholds[CompressionLevel.LEVEL_4_AGGRESSIVE], context_settings.compression_level_4_threshold)

    def test_explicit_thresholds_override_with_settings_fallback(self) -> None:
        compressor = ProgressiveCompressor(model_name="", level_thresholds={CompressionLevel.LEVEL_2_SUMMARY: 0.3})
        self.assertEqual(compressor._level_thresholds[CompressionLevel.LEVEL_2_SUMMARY], 0.3)
        # 缺省项由配置单源补齐
        self.assertEqual(
            compressor._level_thresholds[CompressionLevel.LEVEL_4_AGGRESSIVE],
            context_settings.compression_level_4_threshold,
        )

    def test_shell_engine_uses_settings_config(self) -> None:
        """薄壳内部引擎配置单源：keep_recent / summary_max_length 来自 context_settings。"""
        compressor = ProgressiveCompressor(model_name="")
        self.assertEqual(compressor._engine.config.keep_recent_messages, context_settings.compression_keep_recent)
        self.assertEqual(
            compressor._engine.config.summary_max_length, context_settings.compression_summary_max_length
        )


class LoadResearchContextBypassTests(unittest.TestCase):
    """load_research_context 摘要旁路接线：超长内容调用统一引擎摘要。"""

    def test_long_content_uses_unified_engine_summary(self) -> None:
        from Django_xm.apps.chat.services.context_service import ContextService

        fake_file = mock.MagicMock()
        fake_file.path = Path("research/report.md")
        fake_file.base_dir = Path(".")
        fake_manager = mock.MagicMock()
        fake_manager.list_task_files.return_value = [fake_file]
        fake_manager.read_file_content.return_value = "研究内容" * 4000  # 远超 8000 token

        task = {"query": "研究问题", "final_report": ""}
        with (
            mock.patch.object(ContextService, "CHECKPOINTER_ENABLED", False),
            mock.patch(
                "Django_xm.apps.research.services.cross_app.get_research_task_for_context",
                return_value=task,
            ),
            mock.patch("Django_xm.apps.core.services.file_manager.get_file_manager", return_value=fake_manager),
            mock.patch.object(
                ContextCompressionEngine,
                "summarize_long_text",
                return_value="引擎摘要结果",
            ) as summarize_mock,
        ):
            service = ContextService(user_id=None, thread_id=None)
            result = service.load_research_context("task-1")

        summarize_mock.assert_called_once()
        self.assertEqual(result, "引擎摘要结果")

    def test_short_content_returns_raw_without_engine(self) -> None:
        from Django_xm.apps.chat.services.context_service import ContextService

        fake_file = mock.MagicMock()
        fake_file.path = Path("research/report.md")
        fake_file.base_dir = Path(".")
        fake_manager = mock.MagicMock()
        fake_manager.list_task_files.return_value = [fake_file]
        fake_manager.read_file_content.return_value = "短研究内容"

        task = {"query": "研究问题", "final_report": ""}
        with (
            mock.patch.object(ContextService, "CHECKPOINTER_ENABLED", False),
            mock.patch(
                "Django_xm.apps.research.services.cross_app.get_research_task_for_context",
                return_value=task,
            ),
            mock.patch("Django_xm.apps.core.services.file_manager.get_file_manager", return_value=fake_manager),
            mock.patch.object(
                ContextCompressionEngine, "summarize_long_text", return_value="不应被调用"
            ) as summarize_mock,
        ):
            service = ContextService(user_id=None, thread_id=None)
            result = service.load_research_context("task-1")

        summarize_mock.assert_not_called()
        # 原文直接返回（带 "### 相对路径" 头）
        self.assertTrue(result.endswith("短研究内容"))
        self.assertIn("### research", result)


if __name__ == "__main__":
    unittest.main()
