"""RateLimitMiddleware 限流行为单元测试（ARCH-12 限流双轨收敛后的唯一限流器）。

覆盖：
- 频率超限（> max_calls_per_second/s）：graceful 模式返回循环原因，hard 模式抛 RuntimeError
- 总量超限（>= max_total_calls）：返回循环原因
- 任务取消检查（ARCH-03 依赖倒置）：task_cancelled_checker 由调用方注入，
  ai_engine 不再 import research.models（forbidden 契约）
- 装配链路：create_rate_limit_middleware / RateLimitCapability 均透传 checker

运行：
    python manage.py test Django_xm.apps.ai_engine.tests.test_rate_limit_middleware
"""

from django.test import SimpleTestCase

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
