"""ToolUsageGuard 身份桶会话隔离测试（B1 / 审计 F4 + N-2）。

缺陷（修复前）：chat 流式循环 guard.check 调用点硬编码 thread_id="default"
（loop.py），termination_judge 的 hard-stop 交接亦落在 "default"（graph state
无 thread_id 键）→ 全部会话共享同一去重/循环计数桶：会话 A 触发 DEDUP/BLOCK
后会话 B 同资源调用被误拦，A 的循环证据污染 B 的 hard-stop 判定。

修复契约（本文件锁定）：
1. guard 身份键解析 _resolve_guard_thread_id：configurable.thread_id →
   session_id → "default"（与 judge 侧同源）；
2. 调用点 _evaluate_tool_usage_guard 按真实 thread_id 隔离（两会话互不影响）；
3. hard-stop 交接身份一致：guard 在会话 X 进入 hard-stop 后，只有 X 的
   judge 触发 tool_usage_guard_handoff TERMINATE；
4. judge 身份取值优先级：图运行期 config > 构造注入 > state > "default"
   （子代理 spawn 的 AgentConfig.session_id 继承父会话，subagent_tools/
   spawn.py:136，运行期取值保证父子隔离）。
   E1b 裁决的 _resource_recent 尝试历史语义不受本修复影响（不触碰 guard 内部）。

运行：
    python manage.py test Django_xm.apps.chat.tests.test_guard_thread_identity
"""

import uuid
from unittest import mock

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage

from Django_xm.apps.ai_engine.services.tool_usage_guard import (
    ToolUsageGuard,
    ToolUsageStatus,
    get_tool_usage_guard,
)
from Django_xm.apps.chat.services.stream.loop import (
    _evaluate_tool_usage_guard,
    _resolve_guard_thread_id,
)
from Django_xm.apps.context_manager.services.termination_judge import (
    ContextTerminationJudge,
    TerminationAction,
    TerminationSignal,
)


def _unique_session() -> str:
    return f"sess-{uuid.uuid4().hex[:8]}"


class GuardThreadIdResolutionTests(SimpleTestCase):
    """guard 身份键解析规则（与 judge 侧 _resolve_handoff_thread_id 同源）。"""

    CASES = [
        # (config, session_id, expected) —— configurable.thread_id 优先
        ({"configurable": {"thread_id": "sess-A"}}, "ignored", "sess-A"),
        ({"configurable": {}}, "sess-B", "sess-B"),
        ({"configurable": None}, "sess-C", "sess-C"),
        ({"recursion_limit": 25}, "sess-D", "sess-D"),  # 非 checkpointer 分支无 configurable
        ({"configurable": {"thread_id": ""}}, "", "default"),
        (None, "", "default"),
    ]

    def test_resolution_table(self):
        for config, session_id, expected in self.CASES:
            with self.subTest(config=config, session_id=session_id):
                self.assertEqual(_resolve_guard_thread_id(config, session_id), expected)


class GuardSessionIsolationTests(SimpleTestCase):
    """guard.check 层：两个不同 thread_id 互不影响（表驱动会话对）。"""

    SESSION_PAIRS = [
        ("sess-A", "sess-B"),
        ("chat-20261005-1", "chat-20261005-2"),
        ("u42-abc", "u42-xyz"),
    ]

    @staticmethod
    def _search_args():
        return {"query": "shared query", "max_results": "5"}

    def test_dedup_isolated_between_sessions(self):
        """会话 1 命中 DEDUP 后，会话 2 同资源首次调用仍 ALLOW。"""
        for session_a, session_b in self.SESSION_PAIRS:
            with self.subTest(pair=(session_a, session_b)):
                guard = ToolUsageGuard(dedup_window_seconds=30, same_resource_max=6)
                first = guard.check("web_search", self._search_args(), session_a)
                self.assertEqual(first.status, ToolUsageStatus.ALLOW)
                dup = guard.check("web_search", self._search_args(), session_a)
                self.assertEqual(dup.status, ToolUsageStatus.DEDUP)
                other = guard.check("web_search", self._search_args(), session_b)
                self.assertEqual(other.status, ToolUsageStatus.ALLOW)

    def test_loop_block_and_hard_stop_isolated_between_sessions(self):
        """会话 1 触发 BLOCK + hard-stop，会话 2 同资源不被误拦、无 hard-stop。"""
        for session_a, session_b in self.SESSION_PAIRS:
            with self.subTest(pair=(session_a, session_b)):
                guard = ToolUsageGuard(dedup_window_seconds=30, same_resource_max=6)
                # 连续 blocked_consecutive_max 轮循环 BLOCK 才达 hard-stop 阈值
                for _ in range(guard.blocked_consecutive_max):
                    for _ in range(guard.same_resource_max):
                        decision = guard.check("web_search", self._search_args(), session_a)
                self.assertEqual(decision.status, ToolUsageStatus.BLOCK)
                self.assertTrue(guard.is_in_hard_stop_state(session_a))
                other = guard.check("web_search", self._search_args(), session_b)
                self.assertEqual(other.status, ToolUsageStatus.ALLOW)
                self.assertFalse(guard.is_in_hard_stop_state(session_b))

    def test_shared_identity_bucket_collides(self):
        """机制演示：两"会话"共用同一身份桶（修复前硬编码 default 的形态）
        时必然互相污染——这是身份键必须按会话解析的原因。"""
        guard = ToolUsageGuard(dedup_window_seconds=30)
        shared_bucket = "default"
        guard.check("web_search", self._search_args(), shared_bucket)  # 会话 1
        polluted = guard.check("web_search", self._search_args(), shared_bucket)  # 会话 2
        self.assertEqual(polluted.status, ToolUsageStatus.DEDUP)


class CallSiteIdentityTests(SimpleTestCase):
    """调用点层：_evaluate_tool_usage_guard 按真实 thread_id 隔离。

    修复前函数无 thread_id 参数（内部硬编码 "default"），本组用例因缺参
    TypeError 而失败——即"修复前红"的缺口证明。
    """

    def setUp(self):
        self.guard = get_tool_usage_guard()
        self.session_a = _unique_session()
        self.session_b = _unique_session()

    def tearDown(self):
        # 单例卫生：清除本测试写入的身份桶（避免跨测试残留）
        for tid in (self.session_a, self.session_b):
            self.guard._consecutive_blocked.pop(tid, None)
            with self.guard._lock:
                for key in [k for k in list(self.guard._resource_recent) if k[0] == tid]:
                    self.guard._resource_recent.pop(key, None)
            with self.guard._resource_cache._lock:
                for key in [k for k in list(self.guard._resource_cache._data) if k[0] == tid]:
                    self.guard._resource_cache._data.pop(key, None)

    @staticmethod
    def _search_args():
        return {"query": "call site probe", "max_results": "5"}

    def test_dedup_decision_uses_real_thread_id(self):
        events_first, skip_first = _evaluate_tool_usage_guard(
            "web_search", "call-1", self._search_args(), self.session_a
        )
        self.assertFalse(skip_first)
        self.assertEqual(events_first, [])

        events_dup, skip_dup = _evaluate_tool_usage_guard(
            "web_search", "call-1", self._search_args(), self.session_a
        )
        self.assertTrue(skip_dup)
        self.assertTrue(any(e.get("type") == "tool_usage_dedup" for e in events_dup))

        # 会话 2：同资源同 payload，不受会话 1 的 DEDUP 影响
        events_b, skip_b = _evaluate_tool_usage_guard(
            "web_search", "call-2", self._search_args(), self.session_b
        )
        self.assertFalse(skip_b)
        self.assertEqual(events_b, [])

    def test_hard_stop_state_lands_on_call_site_thread_id(self):
        """连续循环经调用点写入真实 thread_id 桶（供 judge hard-stop 交接）。"""
        guard = self.guard
        for _ in range(guard.blocked_consecutive_max):
            for _ in range(guard.same_resource_max):
                _evaluate_tool_usage_guard(
                    "web_search", "call-1", self._search_args(), self.session_a
                )
        self.assertTrue(guard.is_in_hard_stop_state(self.session_a))
        self.assertFalse(guard.is_in_hard_stop_state(self.session_b))


def _handoff_window_state() -> dict:
    """构造进入 hard-stop 交接分支的 judge 窗口：3 次同参 fs_write_file +
    3 次同参 web_search 交错——同工具同参避开渐进模式检测，异工具交错
    避开连续相同检测，窗口 6 < window_size(10) 跳过多样性检测。"""
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


class TerminationJudgeHandoffIdentityTests(SimpleTestCase):
    """hard-stop 交接身份一致（N-2）：guard 在会话 X hard-stop 后，
    只有 X 的 judge 触发 tool_usage_guard_handoff TERMINATE。"""

    SESSION_PAIRS = [
        ("sess-A", "sess-B"),
        ("chat-20261005-1", "chat-20261005-2"),
    ]

    def setUp(self):
        self.guard = get_tool_usage_guard()
        self._touched: list[str] = []

    def tearDown(self):
        for tid in self._touched:
            self.guard._consecutive_blocked.pop(tid, None)
            with self.guard._lock:
                for key in [k for k in list(self.guard._resource_recent) if k[0] == tid]:
                    self.guard._resource_recent.pop(key, None)
            with self.guard._resource_cache._lock:
                for key in [k for k in list(self.guard._resource_cache._data) if k[0] == tid]:
                    self.guard._resource_cache._data.pop(key, None)

    def _drive_hard_stop(self, thread_id: str) -> None:
        """经 guard.check 真实路径把 thread_id 推入 hard-stop（连续 2 轮循环 BLOCK）。"""
        self._touched.append(thread_id)
        args = {"query": "handoff loop", "max_results": "5"}
        for _ in range(self.guard.blocked_consecutive_max):
            for _ in range(self.guard.same_resource_max):
                self.guard.check("web_search", args, thread_id)

    def test_handoff_identity_consistent_table(self):
        for session_a, session_b in self.SESSION_PAIRS:
            with self.subTest(pair=(session_a, session_b)):
                self._drive_hard_stop(session_a)
                state = _handoff_window_state()

                verdict_a = ContextTerminationJudge(thread_id=session_a).judge(state)
                self.assertEqual(verdict_a.action, TerminationAction.TERMINATE)
                self.assertEqual(verdict_a.signal, TerminationSignal.LOOP_DETECTED)
                self.assertEqual(verdict_a.metadata.get("source"), "tool_usage_guard_handoff")

                # 会话 2 无 hard-stop 证据：不触发交接终止
                verdict_b = ContextTerminationJudge(thread_id=session_b).judge(state)
                self.assertNotEqual(verdict_b.metadata.get("source"), "tool_usage_guard_handoff")
                self.assertEqual(verdict_b.signal, TerminationSignal.CONTINUE)


class JudgeThreadIdResolutionTests(SimpleTestCase):
    """judge 侧身份取值优先级：运行期图 config > 构造注入 > state > default。"""

    def test_runtime_config_wins_over_constructor(self):
        """运行期 config.configurable.thread_id 优先——子代理图（spawn 继承
        父 session_id，spawn.py:136）靠该级实现父子隔离。"""
        judge = ContextTerminationJudge(thread_id="ctor-tid")
        with mock.patch(
            "langgraph.config.get_config",
            return_value={"configurable": {"thread_id": "runtime-tid"}},
        ):
            self.assertEqual(judge._resolve_handoff_thread_id({}), "runtime-tid")

    def test_constructor_fallback_when_outside_graph(self):
        judge = ContextTerminationJudge(thread_id="ctor-tid")
        with mock.patch("langgraph.config.get_config", side_effect=RuntimeError("outside")):
            self.assertEqual(judge._resolve_handoff_thread_id({}), "ctor-tid")

    def test_state_fallback_then_default(self):
        judge = ContextTerminationJudge()
        with mock.patch("langgraph.config.get_config", side_effect=RuntimeError("outside")):
            self.assertEqual(
                judge._resolve_handoff_thread_id({"thread_id": "state-tid"}), "state-tid"
            )
            self.assertEqual(
                judge._resolve_handoff_thread_id({"configurable": {"thread_id": "cfg-tid"}}),
                "cfg-tid",
            )
            self.assertEqual(judge._resolve_handoff_thread_id({}), "default")


class MiddlewareWiringTests(SimpleTestCase):
    """ContextManagerMiddleware 把 thread_id 注入 judge（构造兜底级接线）。"""

    def test_middleware_passes_thread_id_to_judge(self):
        from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware

        mw = ContextManagerMiddleware(model_name="gpt-4o", thread_id="sess-M")
        self.assertEqual(mw._termination_judge._thread_id, "sess-M")
