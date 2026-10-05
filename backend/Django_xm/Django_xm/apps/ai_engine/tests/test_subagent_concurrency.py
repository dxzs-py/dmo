"""子代理并发 Semaphore 上限单元测试（D3/P2-9）。

覆盖：
- 配置层：DEFAULT_SUBAGENT_CONCURRENCY 代码级默认 / settings 覆盖 /
  非法值防呆（<1 按 1 处理、非数值回退默认）；
- 信号量层：进程级 BoundedSemaphore 容量与上限一致、reset 后按新配置重建；
- 排队行为（核心）：上限 2 时第 3 个执行线程被挂起（阻塞在槽获取上，
  不启动事件循环、不执行图），前序子代理释放槽后被唤醒进入执行，
  全部完成后槽位全数归还（BoundedSemaphore 无泄漏）；
- 恢复路径：resume（审批恢复/等待恢复共用同一执行线程入口）同样受
  并发槽约束——上限 1 时持有者未释放前 resume 排队等待。

全部为 mock 级测试（_execute 打桩为事件门控协程，真实模型/图零调用）。

运行：
    python manage.py test Django_xm.apps.ai_engine.tests.test_subagent_concurrency
"""

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from Django_xm.apps.ai_engine.subagent_runtime.adapters.langgraph_adapter import (
    LangGraphAdapter,
)
from Django_xm.apps.ai_engine.subagent_runtime.concurrency import (
    DEFAULT_SUBAGENT_CONCURRENCY,
    get_subagent_concurrency_limit,
    get_subagent_concurrency_semaphore,
    reset_subagent_concurrency_semaphore,
)

# 轮询等待上界（秒）：mock 级排队/唤醒为毫秒级，5s 已含 50 倍以上余量
_WAIT_TIMEOUT = 5.0
_POLL_INTERVAL = 0.01


def _make_instance(thread_id: str) -> SimpleNamespace:
    """构造 _run_in_thread 所需的最小 instance（鸭子类型访问面，不触 DB）。"""
    return SimpleNamespace(thread_id=thread_id, metadata={}, parent_thread_id="parent-test")


def _make_gated_execute(started: list, stop_events: dict):
    """构造打桩 _execute：进入即登记 thread_id，阻塞直至 stop_event 置位。

    用于验证并发槽语义：登记顺序即"实际进入图执行"的顺序，
    stop_event 模拟子代理运行中/终态。
    """

    async def fake_execute(instance, agent_config, configurable, resume_payload):
        started.append(instance.thread_id)
        stop_event = stop_events[instance.thread_id]
        while not stop_event.is_set():
            await asyncio.sleep(_POLL_INTERVAL)

    return fake_execute


class SubAgentConcurrencyConfigTests(SimpleTestCase):
    """配置层：代码级默认 + settings 覆盖 + 非法值防呆。"""

    def test_default_value_is_eight(self):
        """代码级默认 8（审计 PF-03 建议 4-8 上界），test settings 未覆盖时生效。"""
        self.assertEqual(DEFAULT_SUBAGENT_CONCURRENCY, 8)
        self.assertEqual(get_subagent_concurrency_limit(), 8)

    @override_settings(AGENT_SUBAGENT_CONCURRENCY=4)
    def test_settings_override(self):
        self.assertEqual(get_subagent_concurrency_limit(), 4)

    @override_settings(AGENT_SUBAGENT_CONCURRENCY=0)
    def test_zero_clamped_to_one(self):
        """显式 0 按 1 处理（并发上限无禁用语义，无界即审计缺陷本身）。"""
        self.assertEqual(get_subagent_concurrency_limit(), 1)

    @override_settings(AGENT_SUBAGENT_CONCURRENCY=-3)
    def test_negative_clamped_to_one(self):
        self.assertEqual(get_subagent_concurrency_limit(), 1)

    @override_settings(AGENT_SUBAGENT_CONCURRENCY="6")
    def test_numeric_string_accepted(self):
        self.assertEqual(get_subagent_concurrency_limit(), 6)

    @override_settings(AGENT_SUBAGENT_CONCURRENCY="not-a-number")
    def test_non_numeric_falls_back_to_default(self):
        self.assertEqual(get_subagent_concurrency_limit(), 8)


class SubAgentConcurrencySemaphoreTests(SimpleTestCase):
    """进程级信号量：容量定格 / reset 重建。"""

    def setUp(self):
        reset_subagent_concurrency_semaphore()
        self.addCleanup(reset_subagent_concurrency_semaphore)

    @override_settings(AGENT_SUBAGENT_CONCURRENCY=3)
    def test_semaphore_capacity_matches_limit(self):
        semaphore = get_subagent_concurrency_semaphore()
        for _ in range(3):
            self.assertTrue(semaphore.acquire(blocking=False))
        # 容量耗尽后非阻塞获取失败（排队语义由阻塞 acquire 承担）
        self.assertFalse(semaphore.acquire(blocking=False))
        for _ in range(3):
            semaphore.release()

    @override_settings(AGENT_SUBAGENT_CONCURRENCY=2)
    def test_reset_rebuilds_with_current_settings(self):
        first = get_subagent_concurrency_semaphore()
        reset_subagent_concurrency_semaphore()
        second = get_subagent_concurrency_semaphore()
        self.assertIsNot(first, second)
        acquired = 0
        while second.acquire(blocking=False):
            acquired += 1
        self.assertEqual(acquired, 2)
        for _ in range(acquired):
            second.release()


class SubAgentConcurrencyQueueingTests(SimpleTestCase):
    """核心排队行为（mock 级，经 adapter.spawn 真实入口）。"""

    def setUp(self):
        reset_subagent_concurrency_semaphore()
        self.addCleanup(reset_subagent_concurrency_semaphore)

    def _register_stop_cleanup(self, stop_events: dict) -> None:
        """失败路径兜底：置位全部 stop 事件，避免打桩执行线程携槽空转泄漏。"""

        def _set_all():
            for ev in stop_events.values():
                ev.set()

        self.addCleanup(_set_all)

    def _wait_until(self, condition, message: str) -> None:
        deadline = time.monotonic() + _WAIT_TIMEOUT
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(_POLL_INTERVAL)
        self.fail(message)

    def _wait_all_slots_free(self, limit: int) -> None:
        """等待并发槽全数归还（BoundedSemaphore 无泄漏的可观测断言）。"""
        semaphore = get_subagent_concurrency_semaphore()
        deadline = time.monotonic() + _WAIT_TIMEOUT
        while time.monotonic() < deadline:
            acquired = 0
            all_free = True
            for _ in range(limit):
                if semaphore.acquire(blocking=False):
                    acquired += 1
                else:
                    all_free = False
                    break
            for _ in range(acquired):
                semaphore.release()
            if all_free:
                return
            time.sleep(_POLL_INTERVAL)
        self.fail(f"并发槽在 {_WAIT_TIMEOUT}s 内未全数释放（limit={limit}），疑似槽泄漏")

    @override_settings(AGENT_SUBAGENT_CONCURRENCY=2)
    def test_third_spawn_queues_until_slot_released(self):
        """上限 2：第 3 个 spawn 被挂起，前序释放槽后被唤醒进入执行。"""
        adapter = LangGraphAdapter(runtime=mock.MagicMock())
        started: list = []
        stop_events: dict = {"subagent_0": threading.Event(), "subagent_1": threading.Event(), "subagent_2": threading.Event()}
        self._register_stop_cleanup(stop_events)
        fake_execute = _make_gated_execute(started, stop_events)

        with mock.patch.object(adapter, "_execute", fake_execute):
            for i in range(3):
                asyncio.run(adapter.spawn(_make_instance(f"subagent_{i}"), None, None))

            # 恰好 2 个进入执行，第 3 个被并发槽挂起（不会超限执行）
            self._wait_until(lambda: len(started) >= 2, "5s 内未有两个子代理进入执行")
            self.assertEqual(len(started), 2, f"并发超限：同时执行 {started}")
            queued_tid = next(tid for tid in stop_events if tid not in started)

            # 释放一个槽（模拟一个子代理终态）→ 排队者被唤醒
            first_running = started[0]
            stop_events[first_running].set()
            self._wait_until(lambda: queued_tid in started, f"槽释放后排队者 {queued_tid} 未被唤醒")

            # 全部终态：置位剩余 stop 事件，槽全数归还
            for tid, ev in stop_events.items():
                ev.set()
            self._wait_all_slots_free(limit=2)

    @override_settings(AGENT_SUBAGENT_CONCURRENCY=1)
    def test_resume_also_gated_by_semaphore(self):
        """上限 1：持有者未终态前，resume（审批/等待恢复共用入口）排队等待。"""
        adapter = LangGraphAdapter(runtime=mock.MagicMock())
        started: list = []
        stop_events: dict = {"subagent_a": threading.Event(), "subagent_b": threading.Event()}
        self._register_stop_cleanup(stop_events)
        fake_execute = _make_gated_execute(started, stop_events)

        async def fake_rebuild(instance):
            return object()

        instance_a = _make_instance("subagent_a")
        instance_b = _make_instance("subagent_b")

        with (
            mock.patch.object(adapter, "_execute", fake_execute),
            mock.patch.object(adapter, "_rebuild_agent_config", fake_rebuild),
        ):
            asyncio.run(adapter.spawn(instance_a, None, None))
            self._wait_until(lambda: "subagent_a" in started, "5s 内 subagent_a 未进入执行")

            asyncio.run(adapter.resume(instance_b, {"resume": True}))
            # a 尚未终态（stop 未置位）→ 唯一槽被持有 → b 必然未进入执行
            time.sleep(0.1)
            self.assertNotIn("subagent_b", started, "resume 未受并发槽约束，超限执行")

            # a 终态释放槽 → b 恢复执行
            stop_events["subagent_a"].set()
            self._wait_until(lambda: "subagent_b" in started, "槽释放后 resume 恢复未进入执行")

            stop_events["subagent_b"].set()
            self._wait_all_slots_free(limit=1)

    @override_settings(AGENT_SUBAGENT_CONCURRENCY=2)
    def test_exception_path_releases_slot(self):
        """执行异常路径同样释放槽（finally 保证），状态更新经 AsyncMock 走通。"""
        runtime = mock.MagicMock()
        # 异常分支经 loop.run_until_complete(runtime._update_status(...)) 置 FAILED，
        # 需返回可 await 协程（AsyncMock），避免 TypeError 干扰线程收尾。
        runtime._update_status = mock.AsyncMock()
        adapter = LangGraphAdapter(runtime=runtime)

        async def failing_execute(instance, agent_config, configurable, resume_payload):
            raise RuntimeError("模拟子代理执行异常")

        with mock.patch.object(adapter, "_execute", failing_execute):
            asyncio.run(adapter.spawn(_make_instance("subagent_err"), None, None))
            self._wait_all_slots_free(limit=2)
        # 异常路径状态更新被调用（FAILED 终态）
        runtime._update_status.assert_called_once()
