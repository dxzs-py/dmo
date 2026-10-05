"""RateLimitMiddleware 限流行为单元测试（ARCH-12 限流双轨收敛后的唯一限流器）。

覆盖：
- 频率超限（> max_calls_per_second/s）：graceful 模式返回循环原因，hard 模式抛 RuntimeError
- 总量超限（>= max_total_calls）：返回循环原因
- 任务取消检查（ARCH-03 依赖倒置）：task_cancelled_checker 由调用方注入，
  ai_engine 不再 import research.models（forbidden 契约）
- 装配链路：create_rate_limit_middleware / RateLimitCapability 均透传 checker
- E1a：L4 辅助模型 judge 不持锁执行（同步路径）+ 不阻塞事件循环（异步路径 to_thread）
  + fail-open 语义与判定行为保持

运行：
    python manage.py test Django_xm.apps.ai_engine.tests.test_rate_limit_middleware
"""

import asyncio
import threading
from types import SimpleNamespace

from django.test import SimpleTestCase
from langchain_core.messages import ToolMessage

from Django_xm.apps.ai_engine.capabilities.builtin import RateLimitCapability
from Django_xm.apps.ai_engine.guardrails.middleware import (
    RateLimitMiddleware,
    create_rate_limit_middleware,
)


class RateLimitFrequencyTests(SimpleTestCase):
    """限流核心行为（收敛后唯一限流器，行为与收敛前一致）。"""

    def test_per_second_rate_exceeded_returns_reason_gracefully(self):
        """1s 窗口内超过 max_calls_per_second：graceful 模式返回原因字符串。"""
        mw = RateLimitMiddleware(max_calls_per_second=3.0, max_total_calls=10000, graceful_degradation=True)
        reasons = [mw._check_rate_limit("tool") for _ in range(5)]
        self.assertIsNone(reasons[0])
        self.assertIsNone(reasons[1])
        self.assertIsNone(reasons[2])
        self.assertIsNotNone(reasons[3])
        self.assertIn("频率", reasons[3])
        self.assertIsNotNone(reasons[4])

    def test_per_second_rate_hard_mode_raises(self):
        """graceful_degradation=False 时频率超限抛 RuntimeError。"""
        mw = RateLimitMiddleware(max_calls_per_second=1.0, max_total_calls=10000, graceful_degradation=False)
        mw._check_rate_limit("tool")  # 第 1 次：入窗
        with self.assertRaises(RuntimeError):
            mw._check_rate_limit("tool")  # 第 2 次：1 >= 1 超限

    def test_total_calls_exceeded_returns_reason(self):
        """总调用次数达到 max_total_calls：返回总量超限原因。"""
        mw = RateLimitMiddleware(max_calls_per_second=1e9, max_total_calls=10, graceful_degradation=True)
        mw._tool_call_count = 10
        reason = mw._check_rate_limit("tool")
        self.assertIsNotNone(reason)
        self.assertIn("总调用", reason)

    def test_under_limit_returns_none(self):
        """未超限：返回 None（不阻断）。"""
        mw = RateLimitMiddleware(max_calls_per_second=5.0, max_total_calls=300, graceful_degradation=True)
        for _ in range(4):
            self.assertIsNone(mw._check_rate_limit("tool"))


class TaskCancelledCheckerTests(SimpleTestCase):
    """任务取消检查（ARCH-03 依赖倒置：checker 由调用方注入）。"""

    def test_injected_checker_runtime_error_propagates(self):
        """checker 抛 RuntimeError（任务已取消）时向上透传。"""

        def research_style_checker():
            # 模拟调用方（如 research 链路）注入的检查：基于任务状态判定取消
            raise RuntimeError("研究任务已被取消: task-xyz")

        mw = RateLimitMiddleware(task_cancelled_checker=research_style_checker)
        with self.assertRaises(RuntimeError):
            mw._check_task_cancelled()

    def test_injected_checker_other_errors_degrade(self):
        """checker 抛其他异常：降级为不检查，不阻断主流程。"""

        def broken_checker():
            raise ValueError("db unavailable")

        mw = RateLimitMiddleware(task_cancelled_checker=broken_checker)
        mw._check_task_cancelled()  # 不应抛出

    def test_no_checker_is_noop(self):
        """未注入 checker：直接跳过取消检查。"""
        mw = RateLimitMiddleware(task_id="task-xyz")
        mw._check_task_cancelled()  # 不应抛出

    def test_factory_forwards_checker(self):
        """create_rate_limit_middleware 透传 task_cancelled_checker。"""

        def checker():
            return None

        mw = create_rate_limit_middleware(task_cancelled_checker=checker)
        self.assertIs(mw._task_cancelled_checker, checker)

    def test_capability_forwards_checker(self):
        """RateLimitCapability.build_middleware 透传 task_cancelled_checker。"""

        def checker():
            return None

        stack = RateLimitCapability().build_middleware(agent_type="base", task_cancelled_checker=checker)
        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], RateLimitMiddleware)
        self.assertIs(stack[0]._task_cancelled_checker, checker)


def _tool_request(name: str, args: dict, call_id: str) -> SimpleNamespace:
    """构造 wrap_tool_call 所需的最小 tool_call 载荷（鸭子类型，只依赖
    middleware 对 tool_call.tool_call.get(...) 的访问面）。"""
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": call_id})


def _ok_handler(request):
    return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])


async def _aok_handler(request):
    """异步路径 handler（awrap_tool_call 契约要求返回 awaitable）。"""
    return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])


class _LoopJudgeFixture(SimpleTestCase):
    """E1a 公共夹具：构造能触发 L4 judge 的最小场景。

    触发链：连续 2 次同工具（consecutive_same_tool_limit=2）→ L2 模式异常
    → helper_model 注入（绕过 llm_factory）→ L4 judge 执行。
    args 每次不同以避开 L1 精确重复。
    """

    def _make_middleware(self, **overrides) -> RateLimitMiddleware:
        params = dict(
            max_calls_per_second=1e9,
            max_total_calls=100000,
            consecutive_same_tool_limit=2,
            min_calls_before_check=1,
            graceful_degradation=True,
        )
        params.update(overrides)
        return RateLimitMiddleware(**params)

    def _two_calls(self, mw: RateLimitMiddleware, handler) -> None:
        for i in range(2):
            mw.wrap_tool_call(_tool_request("web_search", {"query": f"q{i}"}, f"call-{i}"), handler)


class LoopJudgeLockAndEventLoopTests(_LoopJudgeFixture):
    """E1a 复现：L4 judge 的同步 model.invoke 不得持锁执行、不得跑在事件循环线程。"""

    def test_loop_judge_runs_outside_lock(self):
        """同步路径：judge（invoke）执行期间 self._lock 必须已释放。

        修复前 invoke 在 `with self._lock:` 内被调用，锁内重复 acquire 1s 超时
        返回 False → 断言失败（复现持锁秒级网络调用）。
        """
        mw = self._make_middleware()
        probe = {"lock_was_free": None}

        class _LockProbeModel:
            def invoke(self, messages):
                acquired = mw._lock.acquire(timeout=1.0)
                if acquired:
                    mw._lock.release()
                probe["lock_was_free"] = acquired
                return SimpleNamespace(content="progress")

        mw._get_helper_model = lambda: _LockProbeModel()
        self._two_calls(mw, _ok_handler)
        self.assertTrue(probe["lock_was_free"])

    def test_async_tool_call_runs_judge_off_event_loop(self):
        """异步路径：awrap_tool_call 的 L4 judge 必须在线程池执行。

        事件循环线程内的同步代码调用 asyncio.get_running_loop() 会成功；
        to_thread 工作线程上会抛 RuntimeError。修复前 judge 直接跑在
        事件循环线程（阻塞整个 loop 数秒）→ ran_on_loop=True → 断言失败。
        """

        class _EventLoopProbeModel:
            def __init__(self):
                self.ran_on_loop = None

            def invoke(self, messages):
                try:
                    asyncio.get_running_loop()
                    self.ran_on_loop = True
                except RuntimeError:
                    self.ran_on_loop = False
                return SimpleNamespace(content="progress")

        async def scenario() -> _EventLoopProbeModel:
            model = _EventLoopProbeModel()
            mw = self._make_middleware()
            mw._get_helper_model = lambda: model
            for i in range(2):
                await mw.awrap_tool_call(
                    _tool_request("web_search", {"query": f"q{i}"}, f"call-{i}"), _aok_handler
                )
            return model

        model = asyncio.run(scenario())
        self.assertFalse(model.ran_on_loop)


class LoopJudgeBehaviorPreservationTests(_LoopJudgeFixture):
    """E1a 回归锁定：修复只改变执行位置，不改变判定语义与 fail-open 行为。"""

    def test_judge_exception_fails_open(self):
        """judge invoke 抛异常：fail-open 继续执行（返回 False → 不阻断）。"""

        class _BoomModel:
            def invoke(self, messages):
                raise RuntimeError("helper api down")

        mw = self._make_middleware()
        mw._get_helper_model = lambda: _BoomModel()
        results = []
        for i in range(2):
            results.append(mw.wrap_tool_call(_tool_request("web_search", {"query": f"q{i}"}, f"call-{i}"), _ok_handler))
        # 第 2 次触发 judge → 异常被 _llm_judge_loop 捕获 → fail-open 放行
        self.assertEqual(results[0].content, "ok")
        self.assertEqual(results[1].content, "ok")
        self.assertEqual(mw._llm_judge_count, 1)
        # fail-open 与 progress 同语义：仍进入 10s 冷却（原行为保持）
        self.assertGreater(mw._llm_judge_skip_until, 0.0)

    def test_judge_loop_verdict_blocks_graceful(self):
        """judge 判定 loop：graceful 模式返回循环停止 ToolMessage，不调 handler。"""

        class _LoopModel:
            def invoke(self, messages):
                return SimpleNamespace(content="loop")

        mw = self._make_middleware()
        mw._get_helper_model = lambda: _LoopModel()
        handler_called = []

        def handler(request):
            handler_called.append(True)
            return _ok_handler(request)

        first = mw.wrap_tool_call(_tool_request("web_search", {"query": "q0"}, "call-0"), handler)
        second = mw.wrap_tool_call(_tool_request("web_search", {"query": "q1"}, "call-1"), handler)
        self.assertEqual(first.content, "ok")
        self.assertIn("重复操作循环", second.content)
        self.assertEqual(handler_called, [True])

    def test_judge_progress_sets_skip_cooldown(self):
        """judge 判定 progress：设置 10s 冷却，冷却期内同模式不再触发 judge。"""

        class _ProgressModel:
            invoke_count = 0

            def invoke(self, messages):
                self.invoke_count += 1
                return SimpleNamespace(content="progress")

        model = _ProgressModel()
        mw = self._make_middleware()
        mw._get_helper_model = lambda: model
        for i in range(4):
            result = mw.wrap_tool_call(_tool_request("web_search", {"query": f"q{i}"}, f"call-{i}"), _ok_handler)
            self.assertEqual(result.content, "ok")
        # 第 2 次调用触发一次 judge；冷却期内第 3/4 次不再 judge
        self.assertEqual(model.invoke_count, 1)
        self.assertEqual(mw._llm_judge_count, 1)
        self.assertGreater(mw._llm_judge_skip_until, 0.0)

    def test_judge_budget_exhausted_returns_reason(self):
        """预算耗尽（llm_judge_max_calls）：graceful 返回耗尽原因而非卡死。"""

        class _ProgressModel:
            def invoke(self, messages):
                return SimpleNamespace(content="progress")

        mw = self._make_middleware(llm_judge_max_calls=1)
        mw._get_helper_model = lambda: _ProgressModel()
        self._two_calls(mw, _ok_handler)  # 消耗 1 次预算并进入冷却
        mw._llm_judge_skip_until = 0.0  # 手动越过冷却，直击预算判定
        result = mw.wrap_tool_call(_tool_request("web_search", {"query": "q9"}, "call-9"), _ok_handler)
        self.assertIn("辅助模型判断次数耗尽", result.content)
