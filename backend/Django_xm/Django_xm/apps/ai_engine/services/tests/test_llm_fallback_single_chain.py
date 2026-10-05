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
- FallbackDetectionCallback 判定边界（E3/N-6）：
  * 降级模型名在 registry 内 → provider 反查成功正常亮横幅（主候选路径不受影响）
  * registry 外模型名 / 模型名提取失败 / expected_provider 为空 → 单向漏报边界，
    锁定"无误报方向"契约（修复需跨文件协同消费链，见类 docstring）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.services.tests.test_llm_fallback_single_chain \
        --settings=Django_xm.settings.test
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

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
from Django_xm.apps.ai_engine.services.llm_fallback import FallbackDetectionCallback, LazyFallbackChatModel


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
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager=None, **kwargs
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
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            counter["generate_calls"] += 1
            raise exc

    return _ErrorModel(), counter


def _make_success_model(content: str) -> tuple[_FakeModel, dict[str, int]]:
    """构建 _generate 始终返回固定内容的假模型。"""
    counter = {"generate_calls": 0}

    class _SuccessModel(_FakeModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
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

    def test_circuit_open_second_invoke_skips_primary_and_rebuilds_fallback(self):
        """熔断 OPEN 冷却期内二次 invoke：不再请求主模型，候选**新建**而非复用。

        2026-10-05 勘误：候选实例曾按"同一对话内复用"缓存，但本包装会被多个
        事件循环使用（子代理每线程独立 loop），缓存实例跨 loop 复用其 async
        客户端必报 "Event loop is closed"（实测 DeepSeek 402 降级失效）。
        新契约：factory 每次降级尝试都被调用（新建实例），"跳过主模型"语义不变。
        """
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
        # 候选新建：factory 再次被调用（禁止实例缓存，防跨 loop 复用）
        self.assertEqual(len(factory_calls), factory_calls_snapshot + 1)
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


class LazyFallbackRecoveryResetTests(unittest.TestCase):
    """E1c：主模型恢复成功后 fallback_detected 必须复位。

    背景：get_helper_model 的模块级单例 _helper_model_cache（llm_factory.py:652）
    长期持有 LazyFallbackChatModel 实例——修复前 _on_primary_success 只复位熔断器，
    首次降级后 _fallback_detected 永久为 True，任何读取 fallback_detected /
    get_fallback_info() 的消费者都会持续收到过期降级状态。
    """

    def _build_lazy_model(
        self, primary_model: BaseChatModel, fallback_model: BaseChatModel
    ) -> tuple[LazyFallbackChatModel, list[dict]]:
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

    def test_primary_recovery_resets_fallback_detected(self):
        """某次调用降级 → 后续独立调用主模型成功 → fallback_detected 复位为 False。"""
        counter = {"generate_calls": 0}
        transient_error = _make_status_error(429, RateLimitError)  # 可恢复错误：首次失败不熔断

        class _FlakyPrimary(_FakeModel):
            def _generate(self, messages, stop=None, **kwargs) -> ChatResult:
                counter["generate_calls"] += 1
                if counter["generate_calls"] == 1:
                    raise transient_error
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="primary-ok"))])

        fallback, _fallback_counter = _make_success_model("fallback-response")
        lazy, _factory_calls = self._build_lazy_model(_FlakyPrimary(), fallback)

        # 第一次调用：主模型瞬时失败 → 降级到候选成功
        first = lazy.invoke([HumanMessage("hi")])
        self.assertEqual(first.content, "fallback-response")
        self.assertIs(lazy.fallback_detected, True)
        self.assertEqual(lazy.actual_provider, "fb-provider")

        # 第二次调用：熔断保持 closed（429 可恢复且仅失败 1 次），主模型被再次尝试并成功
        second = lazy.invoke([HumanMessage("hi")])
        self.assertEqual(second.content, "primary-ok")
        self.assertEqual(counter["generate_calls"], 2)

        # E1c 修复点：降级标记随主模型恢复而复位
        self.assertIs(lazy.fallback_detected, False)
        self.assertIsNone(lazy.actual_provider)
        self.assertIsNone(lazy.actual_model)
        self.assertIsNone(lazy.get_fallback_info())


class FallbackDetectionCallbackBoundaryTests(unittest.TestCase):
    """E3/N-6：FallbackDetectionCallback 判定边界锁定（registry 反查依赖）。

    审计结论（部分成立）：actual_provider 由"首个成功 LLM 调用的模型名"经
    get_model_registry 反查（on_llm_end → _infer_provider），且判定要求双方
    均非空。漏报窗口（单向，无误报方向）：
    1. 降级模型名不在 registry（用户配置的降级模型未注册/事后被禁用——
       settings_views 仅校验 provider 不校验 model_name）；
    2. 模型名提取失败（serialized 无 model/model_name/name）；
    3. expected_provider 为空（请求未带 provider_id 时 chat_service 直接传 ""）。
    主候选路径不受影响：HELPER_MODEL_PRIORITY 全部成员在 MODEL_REGISTRY 内，
    且 get_fallback_candidates 已按 provider 可用性过滤。
    未修复原因：消费方（chat stream/fallback.py、finalizer.py）会把
    fallback_info["actual_provider"] 持久化为 default_chat_model 并同步前端
    选择器——registry miss 场景下实际 provider 不可知，放宽判定而不协同
    守卫消费链会把"不报"变成"报 + 写坏配置"。本类锁定当前边界供后续
    修复批次对照。
    """

    _REGISTRY = {
        "openai": {"models": [{"name": "gpt-4o", "capabilities": []}], "default_model": "gpt-4o"},
        "ollama": {"models": [{"name": "qwen3:8b", "capabilities": []}], "default_model": "qwen3:8b"},
    }

    def _run_fallback_sequence(self, fb, fallback_serialized):
        """模拟一次请求内降级序列：主模型（gpt-4o）失败 → 降级模型成功。"""
        with mock.patch(
            "Django_xm.apps.ai_engine.services.llm_fallback.get_model_registry",
            return_value=self._REGISTRY,
        ):
            fb.on_llm_start({"kwargs": {"model": "gpt-4o"}}, ["hi"], run_id="r1")
            fb.on_llm_error("boom", run_id="r1")
            fb.on_llm_start(fallback_serialized, ["hi"], run_id="r2")
            fb.on_llm_end(None, run_id="r2")

    def test_registry_hit_provider_mismatch_reports_fallback(self):
        """主路径核实：降级模型名在 registry 内 → provider 反查成功 → 正常亮横幅。"""
        fb = FallbackDetectionCallback(expected_provider="openai", expected_model="gpt-4o")
        self._run_fallback_sequence(fb, {"kwargs": {"model": "qwen3:8b"}})
        self.assertIs(fb.fallback_detected, True)
        self.assertEqual(fb.actual_provider, "ollama")
        self.assertEqual(fb.actual_model, "qwen3:8b")
        info = fb.get_fallback_info()
        self.assertIsNotNone(info)
        self.assertEqual(info["actual_provider"], "ollama")

    def test_registry_miss_fallback_model_stays_silent_no_misreport(self):
        """N-6 漏报边界①：降级模型名不在 registry → 反查 "" → 不亮横幅，且不误报。"""
        fb = FallbackDetectionCallback(expected_provider="openai", expected_model="gpt-4o")
        self._run_fallback_sequence(fb, {"kwargs": {"model": "unlisted-custom-model"}})
        self.assertIs(fb.fallback_detected, False)
        self.assertIsNone(fb.get_fallback_info())

    def test_model_extraction_failure_stays_silent_no_misreport(self):
        """N-6 漏报边界②：serialized 无模型名信息 → 提取 "" → 不亮横幅，且不误报。"""
        fb = FallbackDetectionCallback(expected_provider="openai", expected_model="gpt-4o")
        self._run_fallback_sequence(fb, {})  # 无 kwargs / name / invocation_params
        self.assertIs(fb.fallback_detected, False)
        self.assertIsNone(fb.get_fallback_info())

    def test_empty_expected_provider_disables_detection(self):
        """N-6 漏报边界③：expected_provider 为空（请求未带 provider_id）→ 检测关闭。"""
        fb = FallbackDetectionCallback(expected_provider="", expected_model="")
        self._run_fallback_sequence(fb, {"kwargs": {"model": "qwen3:8b"}})
        self.assertIs(fb.fallback_detected, False)
        self.assertIsNone(fb.get_fallback_info())

    def test_primary_success_does_not_report(self):
        """无误报方向：首个成功调用即期望模型 → 不亮横幅（即使后续有候选成功）。"""
        fb = FallbackDetectionCallback(expected_provider="openai", expected_model="gpt-4o")
        with mock.patch(
            "Django_xm.apps.ai_engine.services.llm_fallback.get_model_registry",
            return_value=self._REGISTRY,
        ):
            fb.on_llm_start({"kwargs": {"model": "gpt-4o"}}, ["hi"], run_id="r1")
            fb.on_llm_end(None, run_id="r1")
            # 同请求内后续模型调用（如判断器/子任务）不影响首个成功裁决
            fb.on_llm_start({"kwargs": {"model": "qwen3:8b"}}, ["hi"], run_id="r2")
            fb.on_llm_end(None, run_id="r2")
        self.assertIs(fb.fallback_detected, False)
        self.assertEqual(fb.actual_provider, "openai")


class LazyFallbackCrossLoopTests(unittest.TestCase):
    """跨事件循环复用勘误（2026-10-05 实测）：候选实例禁止缓存。

    场景：本包装会被多个事件循环使用（chat 主链 uvicorn 常驻 loop + 每子代理
    独立线程独立 loop，用完即关），候选模型实例若被缓存复用，其 async 客户端
    连接池绑定"首次使用时的循环"，跨 loop 复用必报 "Event loop is closed"
    （实测：DeepSeek 402 → 降级 ollama 失效，子代理以 402 终止）。
    修复后：每次降级尝试经 factory 新建实例（use_cache=False 绕过 factory
    实例缓存）。本测试在两个先后关闭的事件循环中各触发一次降级，断言
    factory 每次都被调用（旧缓存实现下第二次命中缓存，factory 仅调 1 次）。
    """

    def test_fallback_instance_rebuilt_across_loops(self):
        factory_calls: list[dict] = []

        def factory(provider_id: str, model_name: str, **kwargs):
            factory_calls.append({"provider_id": provider_id, "model_name": model_name, **kwargs})
            model, _ = _make_success_model(f"fb-{len(factory_calls)}")
            return model

        primary, _ = _make_error_model(_make_status_error(402))
        lazy = LazyFallbackChatModel(
            primary=primary,
            fallback_candidates=[("fb-provider", "fb-model")],
            factory=factory,
        )
        messages = [HumanMessage("hi")]

        # loop A：主模型 402（永久错误→熔断 OPEN）→ 降级，构建候选实例 A 并使用
        result_a = asyncio.run(lazy.ainvoke(messages))
        # loop A 随 asyncio.run 关闭；loop B 再次降级——必须新建实例
        result_b = asyncio.run(lazy.ainvoke(messages))

        # 旧缓存实现下 loop B 复用实例 A，factory 仅被调 1 次（回归检出力）
        self.assertEqual(len(factory_calls), 2)
        self.assertIsNot(result_a, result_b)
        # factory 必须收到 use_cache=False（绕过 llm_factory 实例缓存）
        self.assertIs(factory_calls[0]["use_cache"], False)
        self.assertIs(factory_calls[1]["use_cache"], False)


if __name__ == "__main__":
    unittest.main()
