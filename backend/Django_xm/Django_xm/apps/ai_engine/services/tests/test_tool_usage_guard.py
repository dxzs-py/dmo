"""ToolUsageGuard 单元测试（ARCH-12 限流双轨收敛后的行为契约）。

收敛后职责边界：
- ToolUsageGuard 不再做任何频率限制（历史 _check_rate_limit 已删除，
  对应 settings 键 tool_usage_rate_limit_* / soft_warning / hard_stop 一并移除）；
- 保留重复调用去重（dedup）与无进展循环阻断（loop BLOCK）；
- agent 工具调用频率限制由 guardrails.RateLimitMiddleware 唯一承担
  （见 tests/test_rate_limit_middleware.py）。

运行：
    python manage.py test Django_xm.apps.ai_engine.services.tests.test_tool_usage_guard
"""

from django.test import SimpleTestCase

from Django_xm.apps.ai_engine.services.tool_usage_guard import (
    ToolUsageGuard,
    ToolUsageStatus,
)


class ToolUsageGuardDedupTests(SimpleTestCase):
    """guard 不再限流、仍去重的行为契约。"""

    def setUp(self):
        self.guard = ToolUsageGuard(dedup_window_seconds=30, same_resource_max=6)

    def test_high_frequency_calls_are_not_rate_limited(self):
        """高频调用不同资源：全部 ALLOW，不出现任何限流阻断。

        历史行为：60s 窗口内超过 rate_limit_max*hard_stop_threshold（约 27 次）
        即 BLOCK。收敛后频率限制不在 guard，80 次应全部放行。
        """
        for i in range(80):
            decision = self.guard.check(
                tool_name="calculator",
                args={"expression": f"1+{i}"},
                thread_id="t1",
            )
            self.assertEqual(decision.status, ToolUsageStatus.ALLOW)
            self.assertNotIn("rate limit", (decision.reason or "").lower())
            self.assertIsNone(decision.short_circuit_response)

    def test_duplicate_resource_within_window_is_deduped(self):
        """短窗口内同资源同 payload：第二次命中 DEDUP（重复调用去重保留）。"""
        first = self.guard.check(
            tool_name="calculator", args={"expression": "2+2"}, thread_id="t1"
        )
        self.assertEqual(first.status, ToolUsageStatus.ALLOW)

        second = self.guard.check(
            tool_name="calculator", args={"expression": "2+2"}, thread_id="t1"
        )
        self.assertEqual(second.status, ToolUsageStatus.DEDUP)
        self.assertIsNotNone(second.short_circuit_response)
        self.assertIsNotNone(second.sse_event)
        self.assertEqual(second.sse_event["type"], "tool_usage_warning")

    def test_no_progress_loop_still_blocks(self):
        """同资源累计 ≥ same_resource_max 且最近 3 次 payload 相同：仍 BLOCK。"""
        statuses = []
        for _ in range(6):
            decision = self.guard.check(
                tool_name="web_search",
                args={"query": "same query", "max_results": "5"},
                thread_id="t1",
            )
            statuses.append(decision.status)
        self.assertEqual(statuses[-1], ToolUsageStatus.BLOCK)
        self.assertIn("no-progress", (decision.reason or ""))

    def test_no_dedup_whitelist_tools_always_allowed(self):
        """白名单工具（如 todo_read）始终 ALLOW 且不去重。"""
        for _ in range(5):
            decision = self.guard.check(
                tool_name="todo_read", args={}, thread_id="t1"
            )
            self.assertEqual(decision.status, ToolUsageStatus.ALLOW)
            self.assertEqual(decision.reason, "whitelisted tool")

    def test_hard_stop_state_only_from_loop_blocks(self):
        """is_in_hard_stop_state 只由循环阻断累加（限流已不在 guard）。"""
        # 连续制造 2 次循环阻断（blocked_consecutive_max 默认 2）
        for _ in range(2):
            for _ in range(6):
                self.guard.check(
                    tool_name="web_search",
                    args={"query": "loop query", "max_results": "5"},
                    thread_id="t2",
                )
        self.assertTrue(self.guard.is_in_hard_stop_state("t2"))
