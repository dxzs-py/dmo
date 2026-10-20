"""D1（AI-2）熔断 OPEN 态逐候选 fallback 回归测试。

契约（remediate-rag-ai-correctness D1）：
1. 熔断 OPEN 态下 fallback 候选逐个尝试——首选候选失败不得使整条链失效
   （修复前 OPEN 态无 try，首选一失败直接 RuntimeError，后续候选永不尝试）；
2. 全部候选失败才上抛 RuntimeError；
3. 流式（stream）路径同语义。

运行方式：
   python manage.py test Django_xm.apps.ai_engine.services.tests.test_llm_fallback_open_circuit --noinput
"""

import unittest

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from openai import APIStatusError


def _make_status_error(status_code: int, cls: type = APIStatusError) -> Exception:
    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return cls("boom", response=response, body=None)


def _make_callable_model(name: str, *, fail_with: Exception | None = None, content: str = ""):
    """构建行为可定制的假聊天模型（pydantic 限制 → 计数器用闭包容器）。"""
    counter = {"calls": 0}

    class _Model(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return name

        def _generate(self, messages, stop=None, **kwargs) -> ChatResult:
            counter["calls"] += 1
            if fail_with is not None:
                raise fail_with
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

        def _stream(self, messages, stop=None, **kwargs):
            # 覆盖默认实现（langchain 1.x 无默认 _stream）：单 chunk 化
            counter["calls"] += 1
            if fail_with is not None:
                raise fail_with
            yield ChatGenerationChunk(message=AIMessageChunk(content=content))

    return _Model(), counter


class OpenCircuitPerCandidateTests(unittest.TestCase):
    """熔断 OPEN 态：逐候选尝试（修复前首选失败即 RuntimeError）。"""

    def _build_lazy(self, *, fb1_fail: Exception | None = None, fb2_fail: Exception | None = None):
        from Django_xm.apps.ai_engine.services.llm_fallback import LazyFallbackChatModel

        primary, primary_counter = _make_callable_model(
            "primary", fail_with=_make_status_error(402)
        )
        fb1, fb1_counter = _make_callable_model(
            "fb1", fail_with=fb1_fail if fb1_fail is not None else RuntimeError("fb1 down")
        )
        fb2, fb2_counter = _make_callable_model(
            "fb2", fail_with=fb2_fail, content="from-fb2"
        )

        models = {"m1": fb1, "m2": fb2}

        def factory(provider_id: str, model_name: str, **kwargs):
            return models[model_name]

        lazy = LazyFallbackChatModel(
            primary=primary,
            fallback_candidates=[("p1", "m1"), ("p2", "m2")],
            factory=factory,
        )
        return lazy, {
            "primary": primary_counter,
            "fb1": fb1_counter,
            "fb2": fb2_counter,
        }

    def test_open_circuit_tries_next_candidate_after_first_fails(self):
        """OPEN 态首选候选失败 → 自动尝试下一候选（修复前直接 RuntimeError）。"""
        lazy, counters = self._build_lazy()

        # 第一次：primary 402 永久错误 → 切候选成功 + 熔断 OPEN
        first = lazy.invoke([HumanMessage("hi")])
        self.assertEqual(first.content, "from-fb2")

        # 第二次：OPEN 态路径——候选 m1 失败 → 应继续尝试 m2 成功
        # （修复前此处直接 RuntimeError("主模型不可用且无可用 fallback 模型")）
        second = lazy.invoke([HumanMessage("hi again")])
        self.assertEqual(second.content, "from-fb2")
        self.assertGreaterEqual(counters["fb1"]["calls"], 1)
        self.assertGreaterEqual(counters["fb2"]["calls"], 1)

    def test_open_circuit_all_candidates_fail_raises(self):
        """全部候选失败才上抛 RuntimeError（无半途成功也不静默吞错）。

        第一次调用：primary 402 → fallback 全失败 → 重抛 402（熔断已 OPEN）；
        第二次调用：OPEN 态全候选失败 → RuntimeError。
        """
        from openai import APIStatusError as _ASE

        lazy, counters = self._build_lazy(fb2_fail=RuntimeError("fb2 down"))
        with self.assertRaises(_ASE):
            lazy.invoke([HumanMessage("hi")])  # 触发熔断 OPEN（fallback 全败，重抛 primary 402）
        with self.assertRaises(RuntimeError):
            lazy.invoke([HumanMessage("hi again")])  # OPEN 态全候选失败
        self.assertGreaterEqual(counters["fb1"]["calls"], 1)
        self.assertGreaterEqual(counters["fb2"]["calls"], 1)

    def test_open_circuit_stream_tries_next_candidate(self):
        """流式路径同语义：候选 m1 流式失败 → 切换 m2 出块。"""
        from Django_xm.apps.ai_engine.services.llm_fallback import LazyFallbackChatModel

        primary, _ = _make_callable_model("primary", fail_with=_make_status_error(402))
        fb1, _fb1_counter = _make_callable_model("fb1", fail_with=RuntimeError("fb1 stream down"))

        class _Fb2Stream(BaseChatModel):
            @property
            def _llm_type(self) -> str:
                return "fb2"

            def _generate(self, messages, stop=None, **kwargs) -> ChatResult:
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="from-fb2"))])

        def factory(provider_id: str, model_name: str, **kwargs):
            return fb1 if model_name == "m1" else _Fb2Stream()

        lazy = LazyFallbackChatModel(
            primary=primary,
            fallback_candidates=[("p1", "m1"), ("p2", "m2")],
            factory=factory,
        )
        lazy.invoke([HumanMessage("hi")])  # 熔断 OPEN

        chunks = list(lazy.stream([HumanMessage("hi")]))
        self.assertTrue(chunks)  # m2 成功出块（修复前 m1 失败即 RuntimeError）


class StreamMidwayFailureTests(unittest.TestCase):
    """D2（AI-3）：流式中途失败不得重放 fallback 全量（内容重复）。"""

    def test_primary_midway_failure_does_not_replay_fallback(self):
        """primary 已输出 2 个 chunk 后失败 → 直接抛出，不追加 fallback 内容。"""
        from Django_xm.apps.ai_engine.services.llm_fallback import LazyFallbackChatModel

        midway_error = RuntimeError("primary stream broken")

        class _PrimaryMidway(BaseChatModel):
            @property
            def _llm_type(self) -> str:
                return "primary"

            def _generate(self, messages, stop=None, **kwargs) -> ChatResult:
                raise midway_error

            def _stream(self, messages, stop=None, **kwargs):
                for i in range(2):
                    yield ChatGeneration(message=AIMessage(content=f"part{i}"))
                raise midway_error

        fb_model, fb_counter = _make_callable_model("fb", content="fallback-full")

        def factory(provider_id: str, model_name: str, **kwargs):
            return fb_model

        lazy = LazyFallbackChatModel(
            primary=_PrimaryMidway(),
            fallback_candidates=[("p1", "fb")],
            factory=factory,
        )

        collected: list[str] = []
        with self.assertRaises(RuntimeError):
            for gen in lazy._stream([HumanMessage("hi")]):
                collected.append(gen.message.content)

        # 输出只含 primary 的半截内容，无 fallback 全量重放
        self.assertEqual(collected, ["part0", "part1"])
        self.assertEqual(fb_counter["calls"], 0)  # 未重放 fallback

    def test_primary_failure_before_output_switches_to_fallback(self):
        """primary 首 chunk 前失败 → 正常切 fallback（保留快速恢复能力）。"""
        from Django_xm.apps.ai_engine.services.llm_fallback import LazyFallbackChatModel

        primary, _ = _make_callable_model("primary", fail_with=RuntimeError("immediate"))
        fb_model, fb_counter = _make_callable_model("fb", content="fallback-full")

        def factory(provider_id: str, model_name: str, **kwargs):
            return fb_model

        lazy = LazyFallbackChatModel(
            primary=primary,
            fallback_candidates=[("p1", "fb")],
            factory=factory,
        )

        chunks = list(lazy._stream([HumanMessage("hi")]))
        contents = [g.message.content for g in chunks]
        self.assertEqual(contents, ["fallback-full"])
        self.assertEqual(fb_counter["calls"], 1)
