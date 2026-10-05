"""研究任务取消级联测试（C1/P2-1：session_executor / session_manager 取消路径）。

覆盖：
- SessionExecutor.stop(research)：调度取消终态化 → 协程终止 + 条件终态化 +
  状态广播 CANCELLED + 审批批次收尾（source=deep_research）+ 子代理终止 +
  checkpointer 释放 + 会话槽移除 + 沙箱清理
- 终态退让：任务已终态（取消瞬间完成）不覆盖、不级联
- 停机路径：stop(finalize_cancel=False) 不触发终态化（保持可恢复态）
- chat 会话：stop() 不进入 research 取消分支（C2：级联走 chat 专属清理）
- 挂起态（suspended）：协程已退出场景注销 awaiter + 终止 fan-out 子代理 + 补清理
- SessionManager：stop_session 无执行器 DB 兜底；start_session 已取消任务拒绝启动

隔离策略：DB/Redis/信令/子代理运行时全部 mock，仅验证编排逻辑
（对齐 test_session_executor.py 既有隔离约定）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_research_cancel --noinput
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.services.fastapi_service.session_executor import SessionExecutor
from Django_xm.services.fastapi_service.session_manager import SessionManager

_MODULE = "Django_xm.services.fastapi_service.session_executor"
_MGR_MODULE = "Django_xm.services.fastapi_service.session_manager"
_TM_MODULE = "Django_xm.apps.research.services.task_manager"


def _make_executor(thread_id="t1", session_type="research", **kw):
    manager = mock.Mock()
    return SessionExecutor(
        manager,
        thread_id=thread_id,
        query=kw.get("query", "q"),
        user_id=kw.get("user_id", 1),
        session_id=kw.get("session_id", "s1"),
        message_id=kw.get("message_id", "m1"),
        params=kw.get("params", {}),
        session_type=session_type,
    )


def _running_subagent(thread_id="sub-1"):
    return SimpleNamespace(thread_id=thread_id, status="running")


def _patch_cascade(stack, *, cancel_return=True):
    """patch 取消级联全部外部依赖（含执行器方法级 mock）。

    abandon_session_pending_approvals 在 session_executor 模块顶层导入，
    须 patch 执行器命名空间（_MODULE）而非来源模块。
    """
    stack.enter_context(
        mock.patch(f"{_TM_MODULE}.cancel_task_if_active", return_value=cancel_return)
    )
    abandon = stack.enter_context(
        mock.patch(f"{_MODULE}.abandon_session_pending_approvals", return_value=0)
    )
    runtime = mock.Mock()
    runtime.list_instances = mock.AsyncMock(return_value=[_running_subagent()])
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
    return abandon, runtime, lifecycle


class TestResearchCancelFinalization(unittest.TestCase):
    """research 会话 stop() → 取消终态化级联。"""

    def test_stop_cascades_full_cleanup(self):
        """running 会话取消：协程终止 + 全量级联 + 状态广播 CANCELLED。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            async def _never_returns(*args, **kwargs):
                await asyncio.Event().wait()

            with ExitStack() as stack:
                abandon, runtime, lifecycle = _patch_cascade(stack)
                publish = stack.enter_context(
                    mock.patch.object(SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock)
                )
                stack.enter_context(
                    mock.patch.object(SessionExecutor, "_persist_research_progress", new_callable=mock.AsyncMock)
                )
                stack.enter_context(
                    mock.patch.object(SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock)
                )
                cleanup = stack.enter_context(
                    mock.patch.object(SessionExecutor, "_schedule_sandbox_cleanup", new_callable=mock.AsyncMock)
                )
                stack.enter_context(mock.patch(f"{_MODULE}._update_task_status", new_callable=mock.AsyncMock))
                stack.enter_context(
                    mock.patch(f"{_MODULE}.execute_research_async", side_effect=_never_returns)
                )
                stack.enter_context(
                    mock.patch.object(SessionExecutor, "_build_agent", new_callable=mock.AsyncMock,
                                      return_value=mock.Mock())
                )
                ex.start()
                await asyncio.sleep(0.05)
                ex.stop()
                # 等待取消终态化后台任务完成
                for _ in range(100):
                    if all(t.done() for t in list(ex._background_tasks)):
                        break
                    await asyncio.sleep(0.01)

            # 协程已被取消终止
            self.assertTrue(ex._run_task.done())
            # 状态广播：启动期 RUNNING（_run_research 入口）+ 取消 CANCELLED；
            # 最后一次必须为 CANCELLED（终态不被覆盖的前提）
            self.assertEqual(publish.await_count, 2)
            self.assertEqual(publish.await_args_list[-1].args[0], "cancelled")
            # 审批批次收尾：source=deep_research
            abandon.assert_called_once_with("deep_research", "t1")
            # 子代理终止（父 awaiter 先注销）
            lifecycle.unregister_parent_awaiter.assert_called_once_with("t1")
            runtime.list_instances.assert_awaited_once_with("t1")
            runtime.terminate.assert_awaited_once_with("sub-1")
            # 会话槽移除 + 沙箱清理（协程 finally 与终态化各移除一次，幂等设计）
            self.assertEqual(ex.manager.remove_session.call_args_list, [mock.call("t1"), mock.call("t1")])
            cleanup.assert_awaited_once()

        asyncio.run(_case())

    def test_stop_yields_when_task_already_terminal(self):
        """取消瞬间任务已终态：条件终态化退让，不广播不级联。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            with ExitStack() as stack:
                abandon, runtime, lifecycle = _patch_cascade(stack, cancel_return=False)
                publish = stack.enter_context(
                    mock.patch.object(SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock)
                )
                cleanup = stack.enter_context(
                    mock.patch.object(SessionExecutor, "_schedule_sandbox_cleanup", new_callable=mock.AsyncMock)
                )
                ex.stop()
                for _ in range(100):
                    if all(t.done() for t in list(ex._background_tasks)):
                        break
                    await asyncio.sleep(0.01)

            publish.assert_not_awaited()
            abandon.assert_not_called()
            runtime.terminate.assert_not_awaited()
            cleanup.assert_not_awaited()

        asyncio.run(_case())

    def test_shutdown_stop_does_not_finalize(self):
        """停机路径 stop(finalize_cancel=False)：不终态化（保持可恢复态，重启恢复续跑）。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            with ExitStack() as stack:
                abandon, runtime, lifecycle = _patch_cascade(stack)
                ex.stop(finalize_cancel=False)
                await asyncio.sleep(0.02)

            abandon.assert_not_called()
            runtime.terminate.assert_not_awaited()
            ex.manager.remove_session.assert_not_called()

        asyncio.run(_case())

    def test_chat_stop_does_not_finalize_research_cancel(self):
        """chat 会话 stop()：不进入 research 取消分支（C2：级联走 chat 专属清理）。

        语义红线：无 research 任务终态化、无 CANCELLED 状态广播；非挂起态
        审批收尾归 chat_executor_core finally（B4），级联不重复调用。
        """
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor(session_type="chat")
            with ExitStack() as stack:
                abandon, runtime, lifecycle = _patch_cascade(stack)
                cancel = stack.enter_context(
                    mock.patch(f"{_TM_MODULE}.cancel_task_if_active")
                )
                publish = stack.enter_context(
                    mock.patch.object(SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock)
                )
                stack.enter_context(
                    mock.patch.object(SessionExecutor, "_schedule_sandbox_cleanup", new_callable=mock.AsyncMock)
                )
                ex.stop()
                for _ in range(100):
                    if all(t.done() for t in list(ex._background_tasks)):
                        break
                    await asyncio.sleep(0.01)

            cancel.assert_not_called()
            publish.assert_not_called()
            abandon.assert_not_called()
            ex.manager.remove_session.assert_not_called()

        asyncio.run(_case())

    def test_suspended_session_cancel_terminates_subagents(self):
        """挂起态（业务等待 fan-in、协程已退出）取消：注销 awaiter + 终止 fan-out 子代理 + 补清理。"""
        async def _case():
            from contextlib import ExitStack

            ex = _make_executor()
            ex._suspended = True  # 协程已退出（挂起态）
            with ExitStack() as stack:
                abandon, runtime, lifecycle = _patch_cascade(stack)
                publish = stack.enter_context(
                    mock.patch.object(SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock)
                )
                release = stack.enter_context(
                    mock.patch.object(SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock)
                )
                cleanup = stack.enter_context(
                    mock.patch.object(SessionExecutor, "_schedule_sandbox_cleanup", new_callable=mock.AsyncMock)
                )
                ex.stop()
                for _ in range(100):
                    if all(t.done() for t in list(ex._background_tasks)):
                        break
                    await asyncio.sleep(0.01)

            self.assertEqual(publish.await_args_list[-1].args[0], "cancelled")
            # 挂起标记已清零：防挂起 watchdog 取消后再次触发 FAILED 覆盖 CANCELLED
            self.assertFalse(ex._suspended)
            self.assertIsNone(ex._suspended_since)
            # 无运行协程可取消（_run_task 为 None）：不抛错，直接级联
            lifecycle.unregister_parent_awaiter.assert_called_once_with("t1")
            runtime.terminate.assert_awaited_once_with("sub-1")
            release.assert_awaited_once()
            ex.manager.remove_session.assert_called_once_with("t1")
            cleanup.assert_awaited_once()

        asyncio.run(_case())


class TestStopSessionFallbackAndGuard(unittest.TestCase):
    """SessionManager：stop_session DB 兜底 + start_session 取消竞态防护。"""

    def setUp(self):
        # 隔离（B4 同源教训）：兜底广播需读 task.session_id（sync_to_async 真实
        # DB 查询会经工作线程打开连接并泄漏到 teardown，占用测试库）。统一 mock
        # ResearchTask.objects —— 本类不触碰 DB。
        p = mock.patch("Django_xm.apps.research.models.ResearchTask.objects")
        research_objects = p.start()
        research_objects.filter.return_value.values_list.return_value.first.return_value = "sess-1"
        self.addCleanup(p.stop)

    def _make_manager(self):
        return SessionManager(max_concurrency=5)

    def test_stop_session_without_executor_falls_back_to_db_cancel(self):
        """无内存执行器（PENDING/信令丢失）：cancel_task_if_active 兜底终态化。"""
        async def _case():
            manager = self._make_manager()
            with mock.patch(f"{_TM_MODULE}.cancel_task_if_active", return_value=True) as cancel, \
                 mock.patch("Django_xm.common.realtime_sync.publish_task_status", new_callable=mock.AsyncMock) as publish:
                await manager.stop_session("t-pending")
            cancel.assert_called_once_with("t-pending")
            publish.assert_awaited_once()
            self.assertEqual(publish.await_args.kwargs["status"], "cancelled")

        asyncio.run(_case())

    def test_stop_session_fallback_noop_when_task_terminal(self):
        """兜底条件终态化退让（任务已终态/不存在）：不广播。"""
        async def _case():
            manager = self._make_manager()
            with mock.patch(f"{_TM_MODULE}.cancel_task_if_active", return_value=False) as cancel, \
                 mock.patch("Django_xm.common.realtime_sync.publish_task_status", new_callable=mock.AsyncMock) as publish:
                await manager.stop_session("t-done")
            cancel.assert_called_once_with("t-done")
            publish.assert_not_awaited()

        asyncio.run(_case())

    def test_stop_session_with_executor_stops_executor(self):
        """有内存执行器：走 executor.stop()（research 触发终态化），不进 DB 兜底。"""
        async def _case():
            manager = self._make_manager()
            executor = mock.Mock()
            manager._sessions["t1"] = executor
            with mock.patch(f"{_TM_MODULE}.cancel_task_if_active") as cancel:
                await manager.stop_session("t1")
            executor.stop.assert_called_once_with()
            cancel.assert_not_called()  # 兜底不触发（槽位移除由终态化任务异步完成）

        asyncio.run(_case())

    def test_start_session_rejects_cancelled_task(self):
        """已取消任务：start_session 拒绝启动（迟到 start 信令/恢复扫描竞态防护）。"""
        async def _case():
            manager = self._make_manager()
            with mock.patch.object(
                SessionManager, "_is_research_task_cancelled", new_callable=mock.AsyncMock, return_value=True
            ), mock.patch(
                f"{_MGR_MODULE}.SessionExecutor"
            ) as ex_cls:
                await manager.start_session({"thread_id": "t1", "query": "q", "session_type": "research"})
            ex_cls.assert_not_called()
            self.assertNotIn("t1", manager._sessions)

        asyncio.run(_case())

    def test_start_session_allows_active_task(self):
        """未取消任务：正常创建执行器（防护不误伤）。"""
        async def _case():
            manager = self._make_manager()
            with mock.patch.object(
                SessionManager, "_is_research_task_cancelled", new_callable=mock.AsyncMock, return_value=False
            ), mock.patch(
                f"{_MGR_MODULE}.SessionExecutor"
            ) as ex_cls:
                ex = mock.Mock()
                ex_cls.return_value = ex
                await manager.start_session({"thread_id": "t1", "query": "q", "session_type": "research"})
            ex_cls.assert_called_once()
            ex.start.assert_called_once()

        asyncio.run(_case())

    def test_start_session_guard_skips_chat(self):
        """chat 会话（无任务表）不做任务取消检查。"""
        async def _case():
            manager = self._make_manager()
            with mock.patch.object(
                SessionManager, "_is_research_task_cancelled", new_callable=mock.AsyncMock
            ) as guard, mock.patch(
                f"{_MGR_MODULE}.SessionExecutor"
            ) as ex_cls:
                ex = mock.Mock()
                ex_cls.return_value = ex
                await manager.start_session({"thread_id": "c1", "query": "q", "session_type": "chat"})
            guard.assert_not_awaited()
            ex_cls.assert_called_once()

        asyncio.run(_case())


if __name__ == "__main__":
    unittest.main()
