"""LLM 熔断分级策略测试（瞬时计数 + half-open 多次探测）。

契约（熔断分级策略重构）：
1. 永久错误（401 认证类，经 _is_permanent_error 单一分类）→ 首次失败立即 OPEN；
2. 瞬时错误（429 限流类）→ 连续 llm_circuit_transient_threshold 次（默认 5）
   才 OPEN，任一次成功清零计数——防止单次 429 把长任务静默打入弱模型降级；
3. half_open：冷却期满进入探测窗口，连续 llm_circuit_half_open_success_threshold
   次（默认 2）成功才 CLOSED；任一探测失败立即重新 OPEN（重置冷却）；
   探测总次数封顶 llm_circuit_half_open_max_probes（默认 3），耗尽未恢复
   重新 OPEN。

运行（backend/Django_xm，conda env langchain_xm）：
    python -m pytest Django_xm/apps/ai_engine/services/tests/test_llm_fallback_circuit_graded.py -q
"""

from __future__ import annotations

import unittest

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from openai import APIStatusError, AuthenticationError, RateLimitError

from Django_xm.apps.ai_engine.services.llm_fallback import LazyFallbackChatModel


def _make_status_error(status_code: int, cls: type = APIStatusError) -> Exception:
    """构造带指定状态码的 OpenAI APIStatusError 族异常实例。"""
    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return cls("boom", response=response, body=None)


def _make_scripted_model(script: list) -> tuple[BaseChatModel, dict]:
    """构建按脚本依次行为的假主模型（熔断分级场景需精确控制每次调用结果）。

    script 元素为 Exception（该次调用抛出）或 str（该次调用成功返回的内容）；
    脚本耗尽后重复最后一项。计数器用闭包可变容器，规避 pydantic
    模型禁止任意实例属性的限制。
    """
    state = {"calls": 0}

    class _ScriptedModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "scripted"

        def _generate(
            self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager=None, **kwargs
        ) -> ChatResult:
            idx = min(state["calls"], len(script) - 1)
            state["calls"] += 1
            step = script[idx]
            if isinstance(step, Exception):
                raise step
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=step))])

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            # 覆盖默认实现（langchain 1.x 无默认 _stream）：单 chunk 化
            idx = min(state["calls"], len(script) - 1)
            state["calls"] += 1
            step = script[idx]
            if isinstance(step, Exception):
                raise step
            yield ChatGenerationChunk(message=AIMessageChunk(content=step))

    return _ScriptedModel(), state


def _make_success_model(content: str = "fb-ok") -> tuple[BaseChatModel, dict]:
    """构建 _generate 始终成功的假候选模型。"""
    counter = {"calls": 0}

    class _FbModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "fb"

        def _generate(
            self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager=None, **kwargs
        ) -> ChatResult:
            counter["calls"] += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    return _FbModel(), counter


def _build_lazy(
    primary: BaseChatModel, *, cooldown: float = 30.0, **extra_fields
) -> tuple[LazyFallbackChatModel, dict]:
    """构建单候选 LazyFallbackChatModel（候选固定成功，熔断参数可覆盖）。"""
    fb_model, fb_counter = _make_success_model()

    def factory(provider_id: str, model_name: str, **kwargs) -> BaseChatModel:
        return fb_model

    lazy = LazyFallbackChatModel(
        primary=primary,
        fallback_candidates=[("fb-provider", "fb-model")],
        factory=factory,
        circuit_breaker_cooldown=cooldown,
        **extra_fields,
    )
    return lazy, fb_counter


class TransientThresholdTests(unittest.TestCase):
    """瞬时错误分级：单次 429 不熔断，连续 5 次才熔断，成功清零计数。"""

    def test_single_429_does_not_open_circuit(self):
        """单次 429（瞬时）→ 熔断保持 closed；本次调用降级，下次主模型被重试。"""
        err429 = _make_status_error(429, RateLimitError)
        primary, primary_counter = _make_scripted_model([err429, "primary-ok"])
        lazy, _fb_counter = _build_lazy(primary)

        first = lazy.invoke([HumanMessage("hi")])
        self.assertEqual(first.content, "fb-ok")  # 本次调用降级到候选
        self.assertEqual(lazy._circuit_state, "closed")  # 但熔断未开

        second = lazy.invoke([HumanMessage("hi")])
        self.assertEqual(second.content, "primary-ok")  # 主模型被再次尝试并成功
        self.assertEqual(primary_counter["calls"], 2)
        self.assertEqual(lazy._circuit_state, "closed")

    def test_five_consecutive_transient_failures_open_circuit(self):
        """连续 5 次 429 → 前 4 次保持 closed，第 5 次 OPEN；OPEN 后主模型不再被调用。"""
        err429 = _make_status_error(429, RateLimitError)
        primary, primary_counter = _make_scripted_model([err429] * 5)
        lazy, _fb_counter = _build_lazy(primary)

        for i in range(4):
            lazy.invoke([HumanMessage("hi")])
            self.assertEqual(lazy._circuit_state, "closed", f"第 {i + 1} 次瞬时失败后不应熔断")

        lazy.invoke([HumanMessage("hi")])  # 第 5 次：达到阈值
        self.assertEqual(lazy._circuit_state, "open")
        self.assertEqual(primary_counter["calls"], 5)

        lazy.invoke([HumanMessage("hi")])  # OPEN 冷却期内：跳过主模型
        self.assertEqual(primary_counter["calls"], 5)

    def test_single_401_opens_circuit_immediately(self):
        """单次 401（永久错误）→ 首次失败立即 OPEN，冷却期内跳过主模型。"""
        err401 = _make_status_error(401, AuthenticationError)
        primary, primary_counter = _make_scripted_model([err401])
        lazy, _fb_counter = _build_lazy(primary)

        first = lazy.invoke([HumanMessage("hi")])
        self.assertEqual(first.content, "fb-ok")
        self.assertEqual(lazy._circuit_state, "open")
        self.assertEqual(primary_counter["calls"], 1)

        lazy.invoke([HumanMessage("hi")])  # OPEN 态：不再请求主模型
        self.assertEqual(primary_counter["calls"], 1)

    def test_success_resets_transient_counter(self):
        """成功清零计数：4 次失败 + 成功 + 4 次失败 + 成功，全程不熔断。"""
        err429 = _make_status_error(429, RateLimitError)
        script = [err429] * 4 + ["primary-ok"] + [err429] * 4 + ["primary-ok"]
        primary, primary_counter = _make_scripted_model(script)
        lazy, _fb_counter = _build_lazy(primary)

        for _ in range(10):
            lazy.invoke([HumanMessage("hi")])

        # 主模型每次都被尝试（共 10 次调用）→ 计数确实被成功清零，从未熔断
        self.assertEqual(primary_counter["calls"], 10)
        self.assertEqual(lazy._circuit_state, "closed")


class HalfOpenProbeTests(unittest.TestCase):
    """half-open 探测窗口：冷却进入、连续 2 次成功恢复、失败重开、次数耗尽重开。"""

    @staticmethod
    def _shift_cooldown_expired(lazy: LazyFallbackChatModel, seconds: float) -> None:
        """把熔断打开时刻前移，模拟冷却期满（避免真实 sleep）。"""
        lazy._circuit_opened_at -= seconds

    def test_cooldown_gate_then_half_open_first_success_does_not_close(self):
        """OPEN 冷却期内跳过主模型；冷却期满转 half_open；首次探测成功不恢复。"""
        err401 = _make_status_error(401, AuthenticationError)
        primary, primary_counter = _make_scripted_model([err401, "probe-ok", "probe-ok"])
        lazy, _fb_counter = _build_lazy(primary, cooldown=30.0)

        lazy.invoke([HumanMessage("hi")])  # 401 → OPEN
        self.assertEqual(lazy._circuit_state, "open")

        lazy.invoke([HumanMessage("hi")])  # 冷却期内：直接降级，不探测
        self.assertEqual(primary_counter["calls"], 1)
        self.assertEqual(lazy._circuit_state, "open")

        self._shift_cooldown_expired(lazy, 31.0)  # 冷却期满
        third = lazy.invoke([HumanMessage("hi")])  # half_open 探测第 1 次
        self.assertEqual(third.content, "probe-ok")
        self.assertEqual(primary_counter["calls"], 2)
        self.assertEqual(lazy._circuit_state, "half_open")  # 连续 1/2 成功，尚未恢复

    def test_half_open_closes_after_two_consecutive_successes(self):
        """half_open 连续 2 次探测成功 → CLOSED，此后主模型正常服务。"""
        err401 = _make_status_error(401, AuthenticationError)
        script = [err401, "probe-ok", "probe-ok", "served-ok"]
        primary, primary_counter = _make_scripted_model(script)
        lazy, _fb_counter = _build_lazy(primary, cooldown=0.0)

        lazy.invoke([HumanMessage("hi")])  # 401 → OPEN
        lazy.invoke([HumanMessage("hi")])  # 探测 1：连续 1/2 成功，仍 half_open
        self.assertEqual(lazy._circuit_state, "half_open")
        lazy.invoke([HumanMessage("hi")])  # 探测 2：连续 2/2 → CLOSED
        self.assertEqual(lazy._circuit_state, "closed")

        result = lazy.invoke([HumanMessage("hi")])  # 恢复后主模型正常服务
        self.assertEqual(result.content, "served-ok")
        self.assertEqual(primary_counter["calls"], 4)

    def test_half_open_probe_failure_reopens_with_reset_cooldown(self):
        """half_open 探测失败（即使瞬时错误）→ 立即重开且冷却时刻被重置。"""
        err401 = _make_status_error(401, AuthenticationError)
        err429 = _make_status_error(429, RateLimitError)
        primary, primary_counter = _make_scripted_model([err401, err429])
        lazy, _fb_counter = _build_lazy(primary, cooldown=30.0)

        lazy.invoke([HumanMessage("hi")])  # 401 → OPEN
        opened_at_first = lazy._circuit_opened_at

        self._shift_cooldown_expired(lazy, 31.0)  # 冷却期满
        lazy.invoke([HumanMessage("hi")])  # half_open 探测 → 429 失败 → 立即重开
        self.assertEqual(lazy._circuit_state, "open")
        # 冷却时刻被重置为"当前时刻"：未重置则仍是前移过的旧时刻（t0 - 31），
        # 必然小于 opened_at_first（Windows monotonic 粒度粗，用 >= 而非 >）
        self.assertGreaterEqual(lazy._circuit_opened_at, opened_at_first)
        self.assertEqual(primary_counter["calls"], 2)

        # 重开后冷却期内主模型不再被调用（冷却从重开时刻重新计满 30s）
        lazy.invoke([HumanMessage("hi")])
        self.assertEqual(primary_counter["calls"], 2)
        self.assertEqual(lazy._circuit_state, "open")

    def test_half_open_probe_budget_exhaustion_reopens(self):
        """探测次数耗尽（达上限仍未连续达标）→ 重新熔断且探测窗口复位。

        用 success_threshold=4 > max_probes=3 的合法构造覆盖（实例化允许
        传参覆盖），使连续成功场景也无法提前关闭，行为化触达次数耗尽分支。
        """
        err401 = _make_status_error(401, AuthenticationError)
        primary, primary_counter = _make_scripted_model([err401, "probe-ok"])
        lazy, _fb_counter = _build_lazy(
            primary,
            cooldown=0.0,
            circuit_breaker_half_open_max_probes=3,
            circuit_breaker_half_open_success_threshold=4,
        )

        lazy.invoke([HumanMessage("hi")])  # 401 → OPEN
        self.assertEqual(lazy._circuit_state, "open")

        for _ in range(3):  # 3 次探测全部成功，但 3 < 4 未达恢复阈值
            lazy.invoke([HumanMessage("hi")])
        self.assertEqual(primary_counter["calls"], 4)
        self.assertEqual(lazy._circuit_state, "half_open")
        self.assertEqual(lazy._half_open_probes, 3)

        lazy.invoke([HumanMessage("hi")])  # 第 4 次：探测耗尽 → 重新熔断
        self.assertEqual(lazy._circuit_state, "open")
        self.assertEqual(primary_counter["calls"], 4)  # 主模型不再被探测
        self.assertEqual(lazy._half_open_probes, 0)  # 探测窗口已复位


if __name__ == "__main__":
    unittest.main()
