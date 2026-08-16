"""SubAgentRuntime 业务等待挂起（B2）单元测试。

覆盖：业务等待 interrupt 判定、wait_for_subagent 结果格式化、
SessionExecutor 挂起注册 awaiter、调度器唤醒（mismatch 忽略 / 正常恢复清理）、
run() finally 挂起态跳过清理（固化规则：保留会话槽 + 不释放 checkpointer）。

隔离策略：不触碰真实 DB/Redis/LLM——execute_research_async / runtime / lifecycle 均 mock。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.services.fastapi_service.tests.test_subagent_wait
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
from Django_xm.apps.tools.base import is_approval_interrupt, is_subagent_wait_interrupt

_MODULE = "Django_xm.services.fastapi_service.session_executor"


def _make_executor(thread_id="t1", **kw):
    manager = mock.Mock()
    return SessionExecutor(
        manager,
        thread_id=thread_id,
        query=kw.get("query", "q"),
        user_id=kw.get("user_id", 1),
        session_id=kw.get("session_id", "s1"),
        message_id=kw.get("message_id", "m1"),
        publish_to_redis=kw.get("publish_to_redis", False),
        params=kw.get("params", {}),
    )


class TestSubAgentWaitInterruptPredicate(unittest.TestCase):
    """interrupt 判定：业务等待与审批中断完全隔离。"""

    def test_subagent_wait_true(self):
        self.assertTrue(is_subagent_wait_interrupt({"_subagent_wait": True, "subagent_thread_id": "s"}))
        self.assertFalse(is_approval_interrupt({"_subagent_wait": True, "subagent_thread_id": "s"}))

    def test_approval_true(self):
        self.assertTrue(is_approval_interrupt({"_approval": True, "requests": []}))
        self.assertFalse(is_subagent_wait_interrupt({"_approval": True, "requests": []}))

    def test_neither(self):
        self.assertFalse(is_subagent_wait_interrupt({}))
        self.assertFalse(is_subagent_wait_interrupt(None))
        self.assertFalse(is_subagent_wait_interrupt({"foo": "bar"}))


class TestWaitForSubAgentFormat(unittest.TestCase):
    """wait_for_subagent 结果格式化（纯静态方法）。"""

    def test_completed(self):
        from Django_xm.apps.agent_hub.subagent_tools.wait import WaitForSubAgentTool

        out = WaitForSubAgentTool._format_resume_value(
            {"subagent_thread_id": "sub1", "status": "completed", "result": "hello"}
        )
        self.assertIn("已完成", out)
        self.assertIn("hello", out)

    def test_failed(self):
        from Django_xm.apps.agent_hub.subagent_tools.wait import WaitForSubAgentTool

        out = WaitForSubAgentTool._format_resume_value(
            {"subagent_thread_id": "sub1", "status": "failed", "result": ""}
        )
        self.assertIn("失败", out)


class TestHandleSuspend(unittest.TestCase):
    """_handle_suspend：置挂起态 + 注册父 awaiter（子代理未终态时）。"""

    def test_registers_awaiter(self):
        async def _case():
            ex = _make_executor()
            result = SimpleNamespace(interrupt_id="int1", subagent_thread_id="sub1")
            runtime = mock.Mock()
            runtime.get_instance = mock.AsyncMock(return_value=SimpleNamespace(status="running"))
            lm = mock.Mock()
            with mock.patch(f"{_MODULE}._update_task_status", new_callable=mock.AsyncMock), mock.patch.object(
                SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock
            ), mock.patch(
                "Django_xm.apps.ai_engine.subagent_runtime.get_subagent_runtime", return_value=runtime
            ), mock.patch(
                "Django_xm.apps.ai_engine.subagent_runtime.lifecycle.get_lifecycle_manager", return_value=lm
            ), mock.patch.object(
                SessionExecutor, "_make_parent_awaiter", return_value=mock.Mock()
            ) as make_awaiter:
                await ex._handle_suspend(result)

            self.assertTrue(ex._suspended)
            self.assertEqual(ex._wait_interrupt_id, "int1")
            self.assertEqual(ex._wait_subagent_thread_id, "sub1")
            lm.register_parent_awaiter.assert_called_once_with("t1", make_awaiter.return_value)

        asyncio.run(_case())


class TestResumeAfterSubAgentWait(unittest.TestCase):
    """调度器唤醒：mismatch 忽略 / 正常恢复并清理。"""

    def test_ignores_non_waiting_subagent(self):
        async def _case():
            ex = _make_executor()
            ex._wait_subagent_thread_id = "sub1"
            ex._suspended = True
            with mock.patch.object(
                SessionExecutor, "_execute_and_handle", new_callable=mock.AsyncMock
            ) as execute, mock.patch.object(
                SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock
            ) as release:
                await ex._resume_after_subagent_wait("sub2", "completed")

            execute.assert_not_awaited()
            release.assert_not_awaited()
            ex.manager.remove_session.assert_not_called()

        asyncio.run(_case())

    def test_resumes_and_cleans_up(self):
        async def _case():
            ex = _make_executor()
            ex._agent = mock.Mock()
            ex._wait_subagent_thread_id = "sub1"
            ex._wait_interrupt_id = "int1"
            ex._suspended = True
            lm = mock.Mock()
            with mock.patch.object(
                SessionExecutor, "_read_subagent_result", new_callable=mock.AsyncMock, return_value="result text"
            ), mock.patch.object(
                SessionExecutor, "_execute_and_handle", new_callable=mock.AsyncMock
            ) as execute, mock.patch.object(
                SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock
            ), mock.patch(
                "Django_xm.apps.ai_engine.subagent_runtime.lifecycle.get_lifecycle_manager", return_value=lm
            ):
                await ex._resume_after_subagent_wait("sub1", "completed")

            execute.assert_awaited_once()
            # Command(resume={interrupt_id: {...}}) 的 interrupt_id 键必须匹配
            args, _ = execute.call_args
            resume_cmd = args[0]
            self.assertEqual(list(resume_cmd.resume.keys()), ["int1"])
            self.assertEqual(resume_cmd.resume["int1"]["status"], "completed")
            self.assertEqual(resume_cmd.resume["int1"]["result"], "result text")
            ex.manager.remove_session.assert_called_once_with("t1")
            lm.unregister_parent_awaiter.assert_called_once_with("t1")

        asyncio.run(_case())


class TestRunCleanupSuspended(unittest.TestCase):
    """run() finally：挂起态跳过 checkpointer 释放 + 会话槽移除（固化规则）。"""

    def test_suspended_skips_cleanup(self):
        async def _case():
            ex = _make_executor()

            async def _suspend():
                ex._suspended = True

            with mock.patch.object(SessionExecutor, "_run_research", side_effect=_suspend), mock.patch.object(
                SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock
            ) as release:
                await ex.run()

            ex.manager.remove_session.assert_not_called()
            release.assert_not_awaited()

        asyncio.run(_case())


if __name__ == "__main__":
    unittest.main()
