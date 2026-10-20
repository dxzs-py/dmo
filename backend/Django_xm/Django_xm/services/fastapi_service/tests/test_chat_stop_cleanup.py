"""chat 停止级联清理测试（C2/P2-6：session_executor chat 停止路径）。

覆盖（审计 P2-6：stop() 原先只终止父协程，不 terminate 运行中子代理，
沙箱容器无主动清理）：
- running 会话停止：等待协程优雅退出后终止运行中/审批挂起子代理 + 沙箱清理；
  终态子代理跳过；不重复审批收尾（非挂起路径归 chat_executor_core finally B4）；
  不移除会话槽（协程退出路径 run finally 负责）；无 research 状态广播
- suspended 会话停止（业务等待子代理、协程已退出）：审批批次收尾（source=chat）
  + 清挂起标记（防 watchdog 停止后重复触发）+ 释放 checkpointer + 移除会话槽
  + 终止 fan-out 子代理 + 沙箱清理
- 停止与挂起注册窄竞态：协程退出时才置挂起标记，级联等待协程死后仍能补清理
- 停机路径 stop(finalize_cancel=False)：不调度 chat 级联（保持可恢复态）
- 子代理终止失败：非致命，级联后续步骤（沙箱清理）继续
- 重复 stop()：仅调度一次级联（chat 无任务表需显式防重）

语义红线锁定：chat 停止不新增终态、无 research CANCELLED 广播、
checkpoint/会话槽归协程退出路径（非挂起时级联不触碰）。

隔离策略：DB/Redis/信令/子代理运行时全部 mock，仅验证编排逻辑
（对齐 test_research_cancel.py 既有隔离约定）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_chat_stop_cleanup --noinput
"""

import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.services.fastapi_service.session_executor import SessionExecutor

_MODULE = "Django_xm.services.fastapi_service.session_executor"


def _make_executor(thread_id="c1", **kw):
    manager = mock.Mock()
    return SessionExecutor(
        manager,
        thread_id=thread_id,
        query=kw.get("query", "hello"),
        user_id=kw.get("user_id", 1),
        session_id=kw.get("session_id", "c1"),
        message_id=kw.get("message_id", "m1"),
        params=kw.get("params", {}),
        session_type="chat",
    )


def _subagents():
    """running / 审批挂起 / 已终态 三种子代理实例（终态应被过滤跳过）。"""
    return [
        SimpleNamespace(thread_id="sub-run", status="running"),
        SimpleNamespace(thread_id="sub-int", status="interrupted_pending_user_input"),
        SimpleNamespace(thread_id="sub-done", status="completed"),
    ]


def _patch_cascade(stack):
    """patch chat 停止级联全部外部依赖。

    abandon_session_pending_approvals 在 session_executor 模块顶层导入，
    须 patch 执行器命名空间（_MODULE）而非来源模块。
    """
    abandon = stack.enter_context(
        mock.patch(f"{_MODULE}.abandon_session_pending_approvals", return_value=0)
    )
    runtime = mock.Mock()
    runtime.list_instances = mock.AsyncMock(return_value=_subagents())
    runtime.terminate = mock.AsyncMock()
    stack.enter_context(
        mock.patch("Django_xm.apps.ai_engine.subagent_runtime.get_subagent_runtime", return_value=runtime)
    )
    lifecycle = mock.Mock()
    stack.enter_context(
        mock.patch(
            "Django_xm.apps.ai_engine.subagent_runtime.lifecycle.get_lifecycle_manager",
            return_value=lifecycle,
        )
    )
    release = stack.enter_context(
        mock.patch.object(SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock)
    )
    cleanup = stack.enter_context(
        mock.patch.object(SessionExecutor, "_schedule_sandbox_cleanup", new_callable=mock.AsyncMock)
    )
    publish = stack.enter_context(
        mock.patch.object(SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock)
    )
    return abandon, runtime, lifecycle, release, cleanup, publish


async def _wait_background(ex, rounds=100):
    """等待后台级联任务完成（对齐 test_research_cancel.py 等待模式）。"""
    for _ in range(rounds):
        if all(t.done() for t in list(ex._background_tasks)):
            return
        await asyncio.sleep(0.01)


class TestChatStopCleanup(unittest.TestCase):
    """chat 会话 stop() → 资源级联清理（子代理终止 + 沙箱清理 + 挂起态补清理）。"""

    def test_running_stop_terminates_subagents_and_cleans_sandbox(self):
        """running 会话停止：协程优雅退出后终止非终态子代理 + 沙箱清理。

        语义红线：非挂起态不收审批尾（B4 归 chat_executor_core finally）、
        不释放 checkpointer / 不移除会话槽（协程退出路径负责）、无 research 广播。
        """
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            # 模拟执行协程：stop() 置位 + 唤醒审批等待事件后快速优雅退出
            wake = asyncio.Event()
            ex._pending_events["batch-1"] = wake

            async def _graceful_exit():
                await wake.wait()

            ex._run_task = asyncio.create_task(_graceful_exit(), name="chat-exec-c1")
            with ExitStack() as stack:
                abandon, runtime, lifecycle, release, cleanup, publish = _patch_cascade(stack)
                ex.stop()
                await _wait_background(ex)

            # 协程未被级联主动取消（优雅语义），经唤醒事件自然退出
            self.assertTrue(ex._run_task.done())
            self.assertFalse(ex._run_task.cancelled())
            # 子代理终止：先注销父 awaiter，再终止非终态实例（终态跳过）
            lifecycle.unregister_parent_awaiter.assert_called_once_with("c1")
            runtime.list_instances.assert_awaited_once_with("c1")
            self.assertEqual(runtime.terminate.await_args_list, [
                mock.call("sub-run"),
                mock.call("sub-int"),
            ])
            # 沙箱清理（容器释放 + 临时目录，复用 C1 延迟清理入口）
            cleanup.assert_awaited_once()
            # 语义红线：不重复审批收尾 / 不触碰会话槽与 checkpointer / 无 research 广播
            abandon.assert_not_called()
            release.assert_not_awaited()
            ex.manager.remove_session.assert_not_called()
            publish.assert_not_called()

        asyncio.run(_case())

    def test_suspended_stop_full_cascade(self):
        """挂起态（业务等待 fan-out、协程已退出）停止：补清理 + 终止子代理。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            ex._suspended = True  # 协程已退出（wait_for_subagent 挂起态）
            ex._suspended_since = time.monotonic()
            with ExitStack() as stack:
                abandon, runtime, lifecycle, release, cleanup, publish = _patch_cascade(stack)
                ex.stop()
                await _wait_background(ex)

            # 挂起标记清零：防挂起 watchdog 在用户停止后再次触发超时终态化广播
            self.assertFalse(ex._suspended)
            self.assertIsNone(ex._suspended_since)
            # 遗留 PENDING 审批批次收尾：source=chat（非挂起路径由 B4 覆盖，此处补挂起缺口）
            abandon.assert_called_once_with("chat", "c1")
            # 释放 checkpointer + 移除会话槽（协程 finally 因挂起被跳过，级联补齐）
            release.assert_awaited_once()
            ex.manager.remove_session.assert_called_once_with("c1")
            # 子代理级联终止 + 沙箱清理
            lifecycle.unregister_parent_awaiter.assert_called_once_with("c1")
            runtime.terminate.assert_any_await("sub-run")
            runtime.terminate.assert_any_await("sub-int")
            self.assertEqual(runtime.terminate.await_count, 2)
            cleanup.assert_awaited_once()
            publish.assert_not_called()

        asyncio.run(_case())

    def test_suspend_after_stop_race_gets_supplementary_cleanup(self):
        """窄竞态（停止后协程仍走到挂起注册）：级联等协程死后按最终挂起态补清理。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            wake = asyncio.Event()
            ex._pending_events["batch-1"] = wake

            async def _exit_suspended_after_stop():
                await wake.wait()
                # 模拟停止与 _handle_chat_wait_suspend 的窄竞态窗口：
                # 停止信令先到，协程仍完成挂起注册后退出
                ex._suspended = True
                ex._suspended_since = time.monotonic()

            ex._run_task = asyncio.create_task(_exit_suspended_after_stop(), name="chat-exec-race")
            with ExitStack() as stack:
                abandon, runtime, _lifecycle, release, cleanup, _publish = _patch_cascade(stack)
                ex.stop()
                await _wait_background(ex)

            self.assertTrue(ex._run_task.done())
            self.assertFalse(ex._suspended)
            self.assertIsNone(ex._suspended_since)
            abandon.assert_called_once_with("chat", "c1")
            release.assert_awaited_once()
            ex.manager.remove_session.assert_called_once_with("c1")
            runtime.terminate.assert_any_await("sub-run")
            cleanup.assert_awaited_once()

        asyncio.run(_case())

    def test_shutdown_stop_skips_chat_cascade(self):
        """停机路径 stop(finalize_cancel=False)：不调度级联（保持可恢复态）。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            ex._suspended = True
            with ExitStack() as stack:
                abandon, runtime, _lifecycle, release, cleanup, _publish = _patch_cascade(stack)
                ex.stop(finalize_cancel=False)
                await asyncio.sleep(0.02)

            runtime.terminate.assert_not_awaited()
            abandon.assert_not_called()
            release.assert_not_awaited()
            cleanup.assert_not_awaited()
            ex.manager.remove_session.assert_not_called()

        asyncio.run(_case())

    def test_terminate_failure_does_not_break_cascade(self):
        """子代理终止失败（非致命）：级联后续沙箱清理继续执行。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            ex._suspended = True
            with ExitStack() as stack:
                abandon, runtime, _lifecycle, _release, cleanup, _publish = _patch_cascade(stack)
                runtime.terminate = mock.AsyncMock(side_effect=RuntimeError("terminate boom"))
                ex.stop()
                await _wait_background(ex)

            runtime.terminate.assert_awaited()
            abandon.assert_called_once_with("chat", "c1")
            cleanup.assert_awaited_once()

        asyncio.run(_case())

    def test_double_stop_schedules_single_cascade(self):
        """重复 stop()：仅调度一次级联（chat 无任务表，显式防重）。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            with ExitStack() as stack:
                _abandon, runtime, lifecycle, _release, cleanup, _publish = _patch_cascade(stack)
                ex.stop()
                ex.stop()
                await _wait_background(ex)

            # 防重：级联只执行一次（每次级联注销一次 awaiter、调度一次沙箱清理）；
            # terminate 2 次 = 单次级联终止 2 个非终态实例（sub-run/sub-int）
            lifecycle.unregister_parent_awaiter.assert_called_once_with("c1")
            self.assertEqual(runtime.terminate.await_count, 2)
            cleanup.assert_awaited_once()

        asyncio.run(_case())


if __name__ == "__main__":
    unittest.main()
