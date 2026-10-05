"""ToolUsageGuard 单元测试（ARCH-12 限流双轨收敛后的行为契约）。

收敛后职责边界：
- ToolUsageGuard 不再做任何频率限制（历史 _check_rate_limit 已删除，
  对应 settings 键 tool_usage_rate_limit_* / soft_warning / hard_stop 一并移除）；
- 保留重复调用去重（dedup）与无进展循环阻断（loop BLOCK）；
- agent 工具调用频率限制由 guardrails.RateLimitMiddleware 唯一承担
  （见 tests/test_rate_limit_middleware.py）。
- E1b：被拦（DEDUP/BLOCK）调用不计入循环窗口（窗口证据只含真实执行的调用）
  + _text_diff_ratio 分母与标称一致（max 分母，去 *2）。

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


_WEB_SEARCH_KEY = ("t1", "web_search", "loop query|5")


class BlockedCallWindowTests(SimpleTestCase):
    """E1b 窗口语义锁定（裁决后的双窗口契约）。

    - general 窗口（_resource_recent）= **尝试历史**：含 DEDUP/BLOCK 被
      拦调用。这是"无进展循环"的证据基础——若被拦调用不入窗，同 payload
      重试永远只命中 DEDUP，凑不齐 same_resource_max，BLOCK 升级链与
      hard-stop（termination_judge 契约）失效。合法重试（payload 变化）
      不命中判定，ALLOW 后复位 _consecutive_blocked。
    - fs 窗口（_path_recent_contents）= **只记真实执行的写入**：BLOCK 的
      写入从未执行，其内容不得进入 diff 证据（见 FsBlockedWriteTests）。
    误杀根因（fs diff 阈值失真）由 _text_diff_ratio 的 E1b 修正解决。
    """

    def setUp(self):
        self.guard = ToolUsageGuard(dedup_window_seconds=30, same_resource_max=6)

    def _check(self, query="loop query"):
        return self.guard.check(
            tool_name="web_search",
            args={"query": query, "max_results": "5"},
            thread_id="t1",
        )

    def test_blocked_call_counted_into_attempt_window(self):
        """general 窗口=尝试历史：BLOCK 的调用留在窗口（BLOCK 升级链基础）。"""
        decision = None
        for _ in range(6):
            decision = self._check()
        self.assertEqual(decision.status, ToolUsageStatus.BLOCK)
        recent = self.guard._resource_recent[_WEB_SEARCH_KEY]
        self.assertEqual(len(recent), 6)  # 尝试历史含被拦调用
        # BLOCK 仍推进 consecutive_blocked（既有 hard-stop 契约保留）
        self.assertEqual(self._consecutive_blocked_value(), 1)

    def _consecutive_blocked_value(self) -> int:
        return self.guard._consecutive_blocked.get("t1", 0)

    def test_dedup_call_counted_into_attempt_window(self):
        """general 窗口=尝试历史：DEDUP 命中的调用也计入（无进展尝试的证据）。"""
        first = self._check()
        self.assertEqual(first.status, ToolUsageStatus.ALLOW)
        second = self._check()
        self.assertEqual(second.status, ToolUsageStatus.DEDUP)
        recent = self.guard._resource_recent[_WEB_SEARCH_KEY]
        self.assertEqual(len(recent), 2)

    def test_block_then_identical_retry_still_blocks_and_counts(self):
        """BLOCK 后原样重试同 payload：仍 BLOCK 且 consecutive_blocked 达 hard-stop
        （无进展循环的升级语义保持，不受窗口修复影响）。"""
        for _ in range(6):
            self._check()  # 第 6 次 BLOCK，cb=1
        retry = self._check()
        self.assertEqual(retry.status, ToolUsageStatus.BLOCK)
        self.assertTrue(self.guard.is_in_hard_stop_state("t1"))

    def test_block_then_changed_retry_resets_counter(self):
        """BLOCK 后换 payload 的合法重试：ALLOW 并复位 consecutive_blocked。"""
        for _ in range(6):
            self._check()  # 第 6 次 BLOCK，cb=1
        changed = self._check(query="different query")
        self.assertEqual(changed.status, ToolUsageStatus.ALLOW)
        self.assertEqual(self._consecutive_blocked_value(), 0)

    def test_allowed_calls_still_recorded_into_window(self):
        """ALLOW 调用入窗（同 resource_key 不同 payload → 5 连 ALLOW）。"""
        for i in range(5):
            decision = self.guard.check(
                tool_name="web_fetch",
                args={"url": "http://example.com", "n": str(i)},  # 同 URL，参数微调
                thread_id="t1",
            )
            self.assertEqual(decision.status, ToolUsageStatus.ALLOW)
        recent = self.guard._resource_recent[("t1", "web_fetch", "http://example.com")]
        self.assertEqual(len(recent), 5)


class FsBlockedWriteTests(SimpleTestCase):
    """E1b 问题 A（fs 路径）：被拦的 fs_write_file 不入 diff 窗口。

    fs 的 diff 证据必须只来自真实执行的写入——BLOCK 的写入从未落盘，
    其内容进入 _path_recent_contents 会污染后续 avg_diff 计算。
    """

    def setUp(self):
        self.guard = ToolUsageGuard(
            dedup_window_seconds=30,
            same_path_max=6,
            same_path_diff_ratio=0.05,
        )

    def _write(self, content: str):
        return self.guard.check(
            tool_name="fs_write_file",
            args={"relative_path": "notes/a.md", "content": content},
            thread_id="t1",
        )

    def _content_window_len(self) -> int:
        window = self.guard._path_recent_contents.get(("t1", "notes/a.md"))
        return len(window) if window else 0

    def test_blocked_write_not_counted_into_path_window(self):
        """真打磨写入（增量 ~1.9% < 5%）第 6 次 BLOCK：BLOCK 内容不入窗。"""
        content = "a" * 96
        decision = None
        for i in range(6):
            if i > 0:
                content = content + str(i - 1) * 2  # 每轮仅追加 2 个新字符 → 打磨
            decision = self._write(content)
        self.assertEqual(decision.status, ToolUsageStatus.BLOCK)
        self.assertEqual(self._content_window_len(), 5)  # 修复前 6（BLOCK 也入窗）→ 复现失败
        self.assertEqual(self.guard._consecutive_blocked.get("t1", 0), 1)

    def test_retry_after_block_with_real_progress_recovers(self):
        """BLOCK 后加大增量的合法重试：ALLOW 且复位（不被抬进 hard-stop）。"""
        content = "a" * 96
        for i in range(6):
            if i > 0:
                content = content + str(i - 1) * 2
            self._write(content)
        self.assertTrue(self.guard._consecutive_blocked.get("t1", 0) >= 1)
        # 合法重试：追加 40 个全新字符（远超 5% 阈值）
        improved = content + "x" * 40
        decision = self._write(improved)
        self.assertEqual(decision.status, ToolUsageStatus.ALLOW)
        self.assertEqual(self.guard._consecutive_blocked.get("t1", 0), 0)
        self.assertFalse(self.guard.is_in_hard_stop_state("t1"))


class TextDiffRatioTests(SimpleTestCase):
    """E1b 问题 B 复现：_text_diff_ratio 分母与标称一致。

    docstring 语义为"非匹配字符数 / max(len(a), len(b))"，原实现除以
    2*base → 实际拦截阈值膨胀约一倍（标称 5% 实际 ~10%），fs 追加场景
    合法的小步写入被误判为打磨循环。
    """

    def test_append_ratio_uses_max_denominator(self):
        """96 字符追加 8 个全新字符 → 8/104 ≈ 7.69%（修复前 8/208 ≈ 3.85%）。"""
        a = "a" * 96
        b = a + "01234567"
        ratio = ToolUsageGuard._text_diff_ratio(a, b)
        self.assertAlmostEqual(ratio, 8 / 104, places=6)  # 修复前 8/208 → 复现失败

    def test_bounds_identical_and_full_replace(self):
        """完全相同 → 0；等长完全替换（双侧计）→ 上界 2.0。"""
        self.assertEqual(ToolUsageGuard._text_diff_ratio("abc", "abc"), 0.0)
        self.assertEqual(ToolUsageGuard._text_diff_ratio("abc", "xyz"), 2.0)
        self.assertEqual(ToolUsageGuard._text_diff_ratio("", ""), 0.0)
        self.assertEqual(ToolUsageGuard._text_diff_ratio("", "abc"), 1.0)


class FsPolishingLoopThresholdTests(SimpleTestCase):
    """E1b 集成复现：fs_write_file 打磨循环阈值恢复标称 5%。

    构造：96 字符文件连续 6 次写入，每次追加 8 个全新字符（相邻真实增量
    8/128≈6.25%、8/136≈5.88%，均高于标称 5%，属于"有进展"的合法小步写入）。
    修复前：分母失真使计算增量 ≈3.1% < 5% → 第 6 次被误判打磨循环 BLOCK。
    修复后：按标称 5% 判定 → 放行。
    """

    def test_nominal_diff_ratio_threshold_restored(self):
        guard = ToolUsageGuard(
            dedup_window_seconds=30,
            same_path_max=6,
            same_path_diff_ratio=0.05,
        )
        content = "a" * 96
        statuses = []
        for i in range(6):
            if i > 0:
                content = content + str(i - 1) * 8  # 每轮追加 8 个全新数字字符
            decision = guard.check(
                tool_name="fs_write_file",
                args={"relative_path": "notes/a.md", "content": content},
                thread_id="t1",
            )
            statuses.append(decision.status)
        self.assertEqual(statuses[-1], ToolUsageStatus.ALLOW)  # 修复前 BLOCK → 复现失败
        self.assertFalse(guard.is_in_hard_stop_state("t1"))
