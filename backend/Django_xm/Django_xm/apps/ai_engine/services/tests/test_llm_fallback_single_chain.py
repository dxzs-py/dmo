"""统一 LLM 降级单链路测试（unify-llm-fallback-single-chain，Task 5.2 + 5.4）。

覆盖：
- classify_exception（Task 5.4）：
  * OpenAI 402 APIStatusError → ModelCallError（payment_error 标记、不可恢复）
  * OpenAI 403 PermissionDeniedError → ModelCallError（permission_error 标记、不可恢复）
  * 对照组：429 RateLimitError → RateLimitExceededError（可恢复，先重试语义不回归）
  * 对照组：401 AuthenticationError → ModelCallError（auth_error 终止标记仅认证失败保留）
- LazyFallbackChatModel（Task 5.2）：
  * 主模型 402（永久错误）→ 切换候选成功 + 熔断直接 OPEN
  * 熔断期内二次 invoke：不再请求主模型、候选缓存复用（factory 只创建一次）
  * 候选模型也失败 → 重抛原始 402 异常，降级标记被重置

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.services.tests.test_llm_fallback_single_chain --settings=Django_xm.settings.test
"""

from __future__ import annotations

import unittest

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from openai import APIStatusError, AuthenticationError, PermissionDeniedError, RateLimitError

from Django_xm.apps.ai_engine.services.exceptions import (
    ModelCallError,
    RateLimitExceededError,
    classify_exception,
)
from Django_xm.apps.ai_engine.services.llm_fallback import LazyFallbackChatModel


def _make_status_error(status_code: int, cls: type = APIStatusError) -> Exception:
    """构造带指定状态码的 OpenAI APIStatusError 族异常实例。"""
    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return cls("boom", response=response, body=None)


class _FakeModel(BaseChatModel):
    """测试用聊天模型基类，_generate 行为由闭包子类定制。"""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs
    ) -> ChatResult:
        raise NotImplementedError("行为由闭包子类定制")


def _make_error_model(exc: Exception) -> tuple[_FakeModel, dict[str, int]]:
    """构建 _generate 始终抛出指定异常的假模型。

    Returns:
        (模型实例, 调用计数器)。计数用闭包可变容器，规避 pydantic
        模型禁止任意实例属性的限制。
    """
    counter = {"generate_calls": 0}

    class _ErrorModel(_FakeModel):
        def _generate(self, messages, stop=None, **kwargs):
            counter["generate_calls"] += 1
            raise exc

    return _ErrorModel(), counter


def _make_success_model(content: str) -> tuple[_FakeModel, dict[str, int]]:
    """构建 _generate 始终返回固定内容的假模型。"""
    counter = {"generate_calls": 0}

    class _SuccessModel(_FakeModel):
        def _generate(self, messages, stop=None, **kwargs):
            counter["generate_calls"] += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    return _SuccessModel(), counter


class ClassifyPaymentAndPermissionTests(unittest.TestCase):
    """classify_exception 对 402/403 的归类（Task 5.4）。"""

    def test_402_classified_as_permanent_payment_error(self):
        """402 → ModelCallError：payment_error 标记、不可恢复、无 auth_error。"""
        classified = classify_exception(_make_status_error(402))
        self.assertIsInstance(classified, ModelCallError)
        # 余额不足为永久错误，不应重试主模型
        self.assertIs(classified.recoverable, False)
        self.assertIs(classified.details.get("payment_error"), True)
        # auth_error 是认证失败终止标记（AgentExecutor FAIL 分支依赖其决定 raise），402 不得携带
        self.assertNotIn("auth_error", classified.details)
        self.assertEqual(classified.error_code, "MODEL_CALL_ERROR")

    def test_403_classified_as_permanent_permission_error(self):
        """403 PermissionDeniedError → ModelCallError：permission_error 标记、不可恢复、无 auth_error。"""
        classified = classify_exception(_make_status_error(403, PermissionDeniedError))
        self.assertIsInstance(classified, ModelCallError)
        self.assertIs(classified.recoverable, False)
        self.assertIs(classified.details.get("permission_error"), True)
        self.assertNotIn("auth_error", classified.details)
        self.assertEqual(classified.error_code, "MODEL_CALL_ERROR")

    def test_429_stays_recoverable_rate_limit(self):
        """对照组：429 仍归类为可恢复限流（先重试语义不回归）。"""
        classified = classify_exception(_make_status_error(429, RateLimitError))
        self.assertIsInstance(classified, RateLimitExceededError)
        self.assertIs(classified.recoverable, True)

    def test_401_keeps_auth_error_marker(self):
        """对照组：401 保留 auth_error 终止标记（仅认证失败携带）。"""
        classified = classify_exception(_make_status_error(401, AuthenticationError))
        self.assertIsInstance(classified, ModelCallError)
        self.assertIs(classified.recoverable, False)
        self.assertIs(classified.details.get("auth_error"), True)


class LazyFallbackPaymentErrorTests(unittest.TestCase):
    """LazyFallbackChatModel 主模型 402 场景（Task 5.2）。"""

    def _build_lazy_model(
        self, primary_model: BaseChatModel, fallback_model: BaseChatModel
    ) -> tuple[LazyFallbackChatModel, list[dict]]:
        """构建单候选 LazyFallbackChatModel，factory 记录每次调用参数。"""
        factory_calls: list[dict] = []

        def factory(provider_id: str, model_name: str, **kwargs) -> BaseChatModel:
            factory_calls.append({"provider_id": provider_id, "model_name": model_name, **kwargs})
            return fallback_model

        lazy = LazyFallbackChatModel(
            primary=primary_model,
            fallback_candidates=[("fb-provider", "fb-model")],
            factory=factory,
        )
        return lazy, factory_calls

    def test_402_triggers_fallback_and_opens_circuit(self):
        """主模型 402 → 切换候选成功；402 属永久错误，首次失败即熔断 OPEN。"""
        primary, _primary_counter = _make_error_model(_make_status_error(402))
        fallback, _fallback_counter = _make_success_model("fallback-response")
        lazy, factory_calls = self._build_lazy_model(primary, fallback)

        result = lazy.invoke([HumanMessage("hi")])

        self.assertEqual(result.content, "fallback-response")
        # 降级状态追踪
        self.assertIs(lazy.fallback_detected, True)
        self.assertEqual(lazy.actual_provider, "fb-provider")
        self.assertEqual(lazy.actual_model, "fb-model")
        # 402 经 classify 后 recoverable=False，_on_primary_failure 首次失败即熔断
        self.assertEqual(lazy._circuit_state, "open")
        # factory 恰好创建一次候选，且以 max_retries=0 创建（候选层不重复重试）
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(factory_calls[0]["provider_id"], "fb-provider")
        self.assertEqual(factory_calls[0]["model_name"], "fb-model")
        self.assertEqual(factory_calls[0]["max_retries"], 0)

    def test_circuit_open_second_invoke_skips_primary_and_reuses_fallback(self):
        """熔断 OPEN 冷却期内二次 invoke：不再请求主模型，候选缓存复用（factory 不再调用）。"""
        primary, primary_counter = _make_error_model(_make_status_error(402))
        fallback, fallback_counter = _make_success_model("fallback-response")
        lazy, factory_calls = self._build_lazy_model(primary, fallback)

        first = lazy.invoke([HumanMessage("hi")])
        self.assertEqual(first.content, "fallback-response")
        self.assertEqual(lazy._circuit_state, "open")

        primary_calls_snapshot = primary_counter["generate_calls"]
        factory_calls_snapshot = len(factory_calls)
        fallback_calls_snapshot = fallback_counter["generate_calls"]

        second = lazy.invoke([HumanMessage("hi")])

        self.assertEqual(second.content, "fallback-response")
        # 熔断期内主模型不再被请求
        self.assertEqual(primary_counter["generate_calls"], primary_calls_snapshot)
        # 候选缓存命中：factory 不再创建，候选模型直接复用
        self.assertEqual(len(factory_calls), factory_calls_snapshot)
        self.assertEqual(fallback_counter["generate_calls"], fallback_calls_snapshot + 1)

    def test_all_candidates_failed_reraises_primary_402(self):
        """候选模型也失败 → 重抛主模型原始 402 异常，降级标记被重置。"""
        primary, _primary_counter = _make_error_model(_make_status_error(402))
        broken_fallback, _ = _make_error_model(RuntimeError("fallback broken"))
        lazy, _factory_calls = self._build_lazy_model(primary, broken_fallback)

        with self.assertRaises(APIStatusError) as ctx:
            lazy.invoke([HumanMessage("hi")])

        # 重抛的是主模型原始 402 异常，上层可按余额不足语义处理
        self.assertEqual(ctx.exception.status_code, 402)
        # 候选失败后降级标记被重置，避免误报降级成功
        self.assertIs(lazy.fallback_detected, False)


if __name__ == "__main__":
    unittest.main()
