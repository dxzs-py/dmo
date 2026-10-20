"""LazyFallbackChatModel.with_structured_output 官方契约测试（Task1 A3）。

重构前缺陷：实现为 ``self.bind(response_format=schema)``——Pydantic 类直传
OpenAI response_format 且无输出解析器，调用必失败，下游
（compression.evaluate_summary_quality 等 with_structured_output 消费方）
被 except 捕获后静默降级/伪造默认通过。
（注：原下游之一 knowledge_graph.extract_with_llm 死代码已随 A13 移除。）

重构后契约（对齐官方 BaseChatModel.with_structured_output）：
1. 返回 Runnable，invoke 输出为**解析后的 Pydantic 实例**；
2. 主候选失败时经 StructuredModelWithFallback 切换降级候选重试；
3. 全部候选失败时抛 RuntimeError（不伪造结果）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_llm_fallback_structured_output
"""

import os
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from Django_xm.apps.ai_engine.services.llm_fallback import (
    LazyFallbackChatModel,
    StructuredModelWithFallback,
)


class _Score(BaseModel):
    value: int = 0


class _FakeStructured:
    """假结构化 Runnable：invoke 返回固定结果或抛错。"""

    def __init__(self, result: Any = None, error: Exception | None = None):
        self._result = result
        self._error = error

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        if self._error is not None:
            raise self._error
        return self._result

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self.invoke(input, config=config, **kwargs)


class _FakeChatModel(BaseChatModel):
    """可控 with_structured_output 行为的假模型。"""

    structured_result: Any = None
    structured_error: Exception | None = None
    build_error: Exception | None = None

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def _generate(self, messages: list, stop: list | None = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        if self.build_error is not None:
            raise self.build_error
        return _FakeStructured(result=self.structured_result, error=self.structured_error)


def _make_lazy(primary: BaseChatModel, candidates: list[tuple[str, str]], factory: Any) -> LazyFallbackChatModel:
    return LazyFallbackChatModel(
        primary=primary,
        fallback_candidates=candidates,
        factory=factory,
        temperature=0.1,
        max_tokens=None,
        streaming=False,
    )


class WithStructuredOutputContractTests(unittest.TestCase):
    """官方契约：解析实例输出 + 降级候选 + 全失败显式报错。"""

    def test_primary_success_returns_parsed_instance(self) -> None:
        primary = _FakeChatModel(structured_result=_Score(value=7))
        factory = mock.MagicMock()
        lazy = _make_lazy(primary, candidates=[("fb", "model-b")], factory=factory)

        structured = lazy.with_structured_output(_Score)
        result = structured.invoke([HumanMessage(content="打分")])

        self.assertIsInstance(result, _Score)
        self.assertEqual(result.value, 7)
        factory.assert_not_called()

    def test_primary_failure_falls_back_to_candidate(self) -> None:
        primary = _FakeChatModel(structured_error=RuntimeError("primary down"))
        fallback_model = _FakeChatModel(structured_result=_Score(value=9))
        factory = mock.MagicMock(return_value=fallback_model)
        lazy = _make_lazy(primary, candidates=[("fb", "model-b")], factory=factory)

        structured = lazy.with_structured_output(_Score)
        result = structured.invoke([HumanMessage(content="打分")])

        self.assertIsInstance(result, _Score)
        self.assertEqual(result.value, 9)
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["provider_id"], "fb")

    def test_primary_build_failure_still_falls_back(self) -> None:
        """主模型 with_structured_output 构建失败（重构前必失败的场景）→ 走候选。"""
        primary = _FakeChatModel(build_error=RuntimeError("response_format 不支持"))
        fallback_model = _FakeChatModel(structured_result=_Score(value=5))
        factory = mock.MagicMock(return_value=fallback_model)
        lazy = _make_lazy(primary, candidates=[("fb", "model-b")], factory=factory)

        structured = lazy.with_structured_output(_Score)
        result = structured.invoke([HumanMessage(content="打分")])

        self.assertIsInstance(result, _Score)
        self.assertEqual(result.value, 5)

    def test_all_candidates_fail_raises(self) -> None:
        """全部候选失败：抛 RuntimeError，不伪造结果。"""
        primary = _FakeChatModel(structured_error=RuntimeError("primary down"))
        factory = mock.MagicMock(side_effect=RuntimeError("候选创建失败"))
        lazy = _make_lazy(primary, candidates=[("fb", "model-b")], factory=factory)

        structured = lazy.with_structured_output(_Score)
        with self.assertRaises(RuntimeError):
            structured.invoke([HumanMessage(content="打分")])

    def test_returns_structured_fallback_orchestrator(self) -> None:
        """返回类型为 StructuredModelWithFallback（多候选编排器，非 RunnableBinding）。"""
        primary = _FakeChatModel(structured_result=_Score(value=1))
        lazy = _make_lazy(primary, candidates=[("fb", "model-b")], factory=mock.MagicMock())
        structured = lazy.with_structured_output(_Score)
        self.assertIsInstance(structured, StructuredModelWithFallback)


class _ScriptedStructured:
    """按脚本依序响应的假结构化 Runnable：记录每次收到的 input，依序抛错/返回。"""

    def __init__(self, script: list[Any]):
        self._script = list(script)
        self.calls: list[Any] = []

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        self.calls.append(input)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self.invoke(input, config=config, **kwargs)


def _validation_error() -> Exception:
    """构造真实 pydantic ValidationError（模拟漏必填字段）。"""
    class _Strict(BaseModel):
        required_field: str  # 必填、无默认值，空输入必然触发 Field required

    try:
        _Strict.model_validate({})
    except Exception as e:
        return e
    raise AssertionError("unreachable")


def _make_orchestrator(scripts: list[list[Any]]) -> tuple[StructuredModelWithFallback, list[_ScriptedStructured]]:
    fakes = [_ScriptedStructured(s) for s in scripts]
    orchestrator = StructuredModelWithFallback(
        structured_models=[(f"p{i}", f"m{i}", f) for i, f in enumerate(fakes)],
        creation_errors=[],
        lazy_candidates=[],
        schema=_Score,
        factory=None,
    )
    return orchestrator, fakes


class SelfHealRetryTests(unittest.TestCase):
    """结构化输出校验失败自愈：注入纠错提示重试 1 次，连接类错误不重试直接切换。"""

    def test_validation_error_selfheal_retry_succeeds(self) -> None:
        """首调 ValidationError → 追加纠错消息重试 → 第二次成功。"""
        orch, fakes = _make_orchestrator([[_validation_error(), _Score(value=7)]])

        result = orch.invoke([HumanMessage(content="打分")])

        self.assertEqual(result.value, 7)
        self.assertEqual(len(fakes[0].calls), 2)
        healed_last = fakes[0].calls[1][-1]
        self.assertEqual(healed_last["role"], "user")
        self.assertIn("未通过 schema 校验", healed_last["content"])

    def test_connection_error_switches_without_selfheal(self) -> None:
        """连接类异常不走自愈，直接切换下一候选（候选 0 仅调用 1 次）。"""
        orch, fakes = _make_orchestrator([
            [ConnectionError("primary down")],
            [_Score(value=9)],
        ])

        result = orch.invoke([HumanMessage(content="打分")])

        self.assertEqual(result.value, 9)
        self.assertEqual(len(fakes[0].calls), 1)

    def test_selfheal_retry_exhausted_switches_candidate(self) -> None:
        """自愈重试仍失败 → 切换下一候选（候选 0 共调用 2 次）。"""
        orch, fakes = _make_orchestrator([
            [_validation_error(), _validation_error()],
            [_Score(value=3)],
        ])

        result = orch.invoke([HumanMessage(content="打分")])

        self.assertEqual(result.value, 3)
        self.assertEqual(len(fakes[0].calls), 2)
        self.assertEqual(len(fakes[1].calls), 1)

    def test_unrecognized_input_type_skips_selfheal(self) -> None:
        """无法安全追加纠错消息的输入类型 → 不自愈，直接抛原异常。"""
        orch, fakes = _make_orchestrator([[_validation_error(), _Score(value=1)]])

        with self.assertRaises(Exception):  # noqa: B017 -- 锁定"原样抛出"边界，上游异常类型非本测试关注点
            orch.invoke({"prompt_vars": {"x": 1}})

        self.assertEqual(len(fakes[0].calls), 1)

    def test_string_input_selfheal_appends_hint(self) -> None:
        """纯字符串输入自愈：纠错提示拼接到字符串尾部。"""
        orch, fakes = _make_orchestrator([[_validation_error(), _Score(value=5)]])

        result = orch.invoke("请打分")

        self.assertEqual(result.value, 5)
        self.assertIn("未通过 schema 校验", fakes[0].calls[1])

    def test_ainvoke_selfheal_retry_succeeds(self) -> None:
        """异步路径自愈与同步语义一致。"""
        import asyncio

        orch, fakes = _make_orchestrator([[_validation_error(), _Score(value=8)]])

        result = asyncio.run(orch.ainvoke([HumanMessage(content="打分")]))

        self.assertEqual(result.value, 8)
        self.assertEqual(len(fakes[0].calls), 2)


if __name__ == "__main__":
    unittest.main()
