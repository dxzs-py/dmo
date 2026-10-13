"""runtime_providers 运行期注入守护测试（任务 4.1 环 1 破环配套）。

context_manager 禁止 import ai_engine（.importlinter 契约
context-manager-not-dependent-on-ai-engine），模型解析链 / Store 工厂 /
ToolUsageGuard 单例 / Embedding 服务 / 文档上下文构建均改为运行期 provider
注入。本文件锁定：

1. 接线存在性：django.setup()（apps.ready()）后六个 provider 均已注册；
2. 消费语义：压缩模型走注入 provider；未注册时压缩走本地摘要降级、
   评估返回默认评分、Store 返回 None、guard 交接按注入单例判定。

各用例用 try/finally 恢复 provider 原值，避免跨用例污染。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_runtime_providers
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.context_manager.services.compression import (
    CompressionConfig,
    ContextCompressionEngine,
    SummaryQuality,
)
from Django_xm.apps.context_manager.services.knowledge_graph import KnowledgeGraphStore
from Django_xm.apps.context_manager.services.runtime_providers import (
    get_compression_model_provider,
    get_document_context_provider,
    get_embedding_provider,
    get_evaluation_model_provider,
    get_store_provider,
    get_tool_usage_guard_provider,
    set_compression_model_provider,
    set_document_context_provider,
    set_embedding_provider,
    set_evaluation_model_provider,
    set_store_provider,
    set_tool_usage_guard_provider,
)
from Django_xm.apps.context_manager.services.termination_judge import (
    ContextTerminationJudge,
    TerminationAction,
    TerminationSignal,
)


def _fake_llm(content: str):
    response = mock.MagicMock()
    response.content = content
    model = mock.MagicMock()
    model.invoke.return_value = response
    return model


class ProviderWiringTests(unittest.TestCase):
    """apps.ready() 接线存在性：六个 provider 全部注册。"""

    def test_all_providers_registered_after_setup(self) -> None:
        self.assertIsNotNone(get_compression_model_provider())
        self.assertIsNotNone(get_evaluation_model_provider())
        self.assertIsNotNone(get_store_provider())
        self.assertIsNotNone(get_tool_usage_guard_provider())
        self.assertIsNotNone(get_embedding_provider())
        self.assertIsNotNone(get_document_context_provider())


class _ProviderOverrideMixin:
    """provider 覆盖/恢复工具：保存原值，finally 恢复。"""

    def _override(self, setter, getter, value):
        original = getter()
        setter(value)
        self.addCleanup(setter, original)


class CompressionModelProviderTests(_ProviderOverrideMixin, unittest.TestCase):
    """压缩模型 provider 消费语义。"""

    def _make_engine(self) -> ContextCompressionEngine:
        return ContextCompressionEngine(
            CompressionConfig(max_context_tokens=100, token_threshold_ratio=0.8, keep_recent_messages=2)
        )

    def test_injected_provider_used_for_summary(self) -> None:
        fake_model = _fake_llm("注入模型生成的摘要")
        self._override(set_compression_model_provider, get_compression_model_provider, lambda _lw: fake_model)
        engine = self._make_engine()

        summary = engine._generate_summary([{"role": "user", "content": "如何部署服务"}], [])

        self.assertEqual(summary, "注入模型生成的摘要")
        fake_model.invoke.assert_called_once()

    def test_missing_provider_falls_back_to_local_summary(self) -> None:
        self._override(set_compression_model_provider, get_compression_model_provider, None)
        engine = self._make_engine()
        messages = [{"role": "user", "content": "如何部署服务到生产环境"}]

        summary = engine._generate_summary(messages, [])

        # 未注入 → RuntimeError → 既有本地摘要降级路径（"对话摘要：…"）
        self.assertTrue(summary.startswith("对话摘要"))
        self.assertIn("如何部署服务到生产环境", summary)


class EvaluationModelProviderTests(_ProviderOverrideMixin, unittest.TestCase):
    """评估模型 provider 消费语义。"""

    def test_injected_provider_used_for_quality(self) -> None:
        quality = SummaryQuality(completeness=0.9, accuracy=0.9, conciseness=0.9, overall=0.9, passed=True)
        fake_model = mock.MagicMock()
        structured = mock.MagicMock()
        structured.invoke.return_value = quality
        fake_model.with_structured_output.return_value = structured
        self._override(set_evaluation_model_provider, get_evaluation_model_provider, lambda: fake_model)
        engine = ContextCompressionEngine(CompressionConfig())

        result = engine.evaluate_summary_quality(
            [{"role": "user", "content": "问题"}], "评估的摘要"
        )

        self.assertEqual(result, quality)
        fake_model.with_structured_output.assert_called_once()

    def test_missing_provider_treated_as_not_passed(self) -> None:
        self._override(set_evaluation_model_provider, get_evaluation_model_provider, None)
        engine = ContextCompressionEngine(CompressionConfig())

        result = engine.evaluate_summary_quality([{"role": "user", "content": "问题"}], "摘要")

        # 未注入 → 明确失败语义：评估不可用视为未通过（禁止伪造默认通过分）
        self.assertFalse(result.passed)
        self.assertEqual(result.overall, 0.0)


class StoreProviderTests(_ProviderOverrideMixin, unittest.TestCase):
    """Store 工厂 provider 消费语义（与原 ensure_store 等价：首用注入并缓存）。"""

    def test_provider_result_cached_on_instance(self) -> None:
        sentinel = object()
        factory = mock.MagicMock(return_value=sentinel)
        self._override(set_store_provider, get_store_provider, factory)
        kg_store = KnowledgeGraphStore(store=None)

        first = kg_store._ensure_store()
        second = kg_store._ensure_store()

        self.assertIs(first, sentinel)
        self.assertIs(second, sentinel)
        factory.assert_called_once()

    def test_missing_provider_returns_none(self) -> None:
        self._override(set_store_provider, get_store_provider, None)
        kg_store = KnowledgeGraphStore(store=None)

        self.assertIsNone(kg_store._ensure_store())


class ToolUsageGuardProviderTests(_ProviderOverrideMixin, unittest.TestCase):
    """guard 交接 provider 消费语义（hard-stop 单例判定链）。"""

    @staticmethod
    def _handoff_window_state() -> dict:
        # 与 chat/tests/test_guard_thread_identity._handoff_window_state 同构：
        # fs_write_file×3 与 web_search×3 交错，避开渐进模式/连续相同/多样性检测，
        # 使流程直达 handoff 分支（要求最近窗口含 ≥3 次 fs_write_file）。
        from langchain_core.messages import AIMessage

        messages = []
        for i in range(3):
            messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "fs_write_file",
                            "args": {"relative_path": "notes/a.md", "content": "same"},
                            "id": f"fs-{i}",
                        }
                    ],
                )
            )
            messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "web_search",
                            "args": {"query": "same", "max_results": "5"},
                            "id": f"ws-{i}",
                        }
                    ],
                )
            )
        return {"messages": messages}

    def test_injected_guard_hard_stop_triggers_terminate(self) -> None:
        guard = mock.MagicMock()
        guard.is_in_hard_stop_state.return_value = True
        self._override(set_tool_usage_guard_provider, get_tool_usage_guard_provider, lambda: guard)
        judge = ContextTerminationJudge(thread_id="sess-X")

        with mock.patch("langgraph.config.get_config", side_effect=RuntimeError("outside graph")):
            verdict = judge._check_loop_detected(self._handoff_window_state())

        self.assertIsNotNone(verdict)
        self.assertEqual(verdict.action, TerminationAction.TERMINATE)
        self.assertEqual(verdict.signal, TerminationSignal.LOOP_DETECTED)
        self.assertEqual(verdict.metadata.get("source"), "tool_usage_guard_handoff")
        guard.is_in_hard_stop_state.assert_called_once_with("sess-X")


class EmbeddingProviderTests(_ProviderOverrideMixin, unittest.TestCase):
    """Embedding provider 缺失时按既有降级路径回退（E5-03 留痕语义不变）。"""

    def test_missing_provider_similarity_degrades_to_none(self) -> None:
        self._override(set_embedding_provider, get_embedding_provider, None)

        result = ContextTerminationJudge._embedding_similarity("文本A", "文本B")

        self.assertIsNone(result)


class DocumentContextProviderTests(_ProviderOverrideMixin, unittest.TestCase):
    """文档上下文 provider 注册通道存在性（接线由 attachments ready() 承担）。"""

    def test_override_round_trip(self) -> None:
        provider = lambda user_id, store: "文档上下文"  # noqa: E731
        self._override(set_document_context_provider, get_document_context_provider, provider)
        self.assertIs(get_document_context_provider(), provider)


if __name__ == "__main__":
    unittest.main()
