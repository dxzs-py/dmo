"""ContextCompressionEngine 统一能力测试。

Task1 官方语义重构后边界说明：
- agent 入口 state 压缩已替换为官方 SummarizationMiddleware（薄壳
  ProgressiveCompressor 及其分级阈值配置单源测试随之删除）；
- 本文件锁定引擎保留能力：BaseMessage→dict 适配单源（messages_to_dicts）
  与统一摘要能力 summarize_long_text（跨会话记忆/研究上下文旁路复用）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.context_manager.tests.test_engine_unification
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.messages import HumanMessage

from Django_xm.apps.context_manager.services.compression import (
    ContextCompressionEngine,
    messages_to_dicts,
)

def _fake_llm(content: str):
    response = mock.MagicMock()
    response.content = content
    model = mock.MagicMock()
    model.invoke.return_value = response
    return model


class AdapterTests(unittest.TestCase):
    """BaseMessage → dict 适配单源。"""

    def test_messages_to_dicts_preserves_fields(self) -> None:
        msg = HumanMessage(content="问题", id="m1")
        msg.additional_kwargs["memory_tier"] = "long_term"
        dicts = messages_to_dicts([msg])
        self.assertEqual(dicts[0]["role"], "user")
        self.assertEqual(dicts[0]["content"], "问题")
        self.assertEqual(dicts[0]["id"], "m1")
        self.assertEqual(dicts[0]["memory_tier"], "long_term")


class SummarizeLongTextTests(unittest.TestCase):
    """统一摘要能力 summarize_long_text（跨会话记忆/研究上下文旁路复用）。"""

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


if __name__ == "__main__":
    unittest.main()
