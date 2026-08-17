"""SessionExecutor / SessionManager 单元测试（Task 8 / 9.1）。

覆盖：审批挂起与信令唤醒（批次完整性）、超时自愈、协程结束清理（成功/失败/取消）、
启动恢复（recover_unfinished）、会话注册/并发限流/移除幂等、on_approval_signal 唤醒正确批次。

隔离策略：测试不触碰真实 DB/Redis——collect_batch_decisions / self_heal_expired_approvals /
finalize_batch_approvals / execute_research_async 等外部依赖全部 mock，仅验证执行器编排逻辑。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.services.fastapi_service.tests.test_session_executor
"""

import asyncio
import contextlib
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


def _result(success=True, error_message=""):
    return SimpleNamespace(
        success=success,
        final_report="final report" if success else "",
        files=None,
        state_files=None,
        usage_data=None,
        model_name="",
        error_message=error_message,
        reasoning="reasoning" if success else "",
        raw_result=None,
        suspended=False,
        subagent_thread_id="",
        interrupt_id="",
    )


class TestWaitForBatchDecision(unittest.TestCase):
    """挂起等待批次决策：信令唤醒必须重新校验批次完整性（不早退）。"""

    def _patch_loop(self, collect=None, heal=None, finalize=None, poll=0.01):
        return mock.patch.multiple(
            _MODULE,
            collect_batch_decisions=collect if collect is not None else mock.DEFAULT,
            self_heal_expired_approvals=heal if heal is not None else mock.DEFAULT,
            finalize_batch_approvals=finalize if finalize is not None else mock.DEFAULT,
            _BATCH_POLL_INTERVAL=poll,
        )

    def test_all_resolved_immediately(self):
        """DB 中批次已全部决断：立即返回 decisions，不做自愈。"""
        async def _case():
            ex = _make_executor()
            full = {"r1": {"t1": True, "t2": False}}
            heal = mock.Mock()
            fin = mock.Mock()
            with self._patch_loop(
                collect=mock.Mock(return_value=(full, True)),
                heal=heal,
                finalize=fin,
            ):
                decisions = await ex._wait_for_batch_decision("g1")
            assert decisions == full
            heal.assert_not_called()
            fin.assert_called_once_with(full, "deep_research", "t1")

        asyncio.run(_case())

    def test_partial_decision_does_not_early_return(self):
        """信令到达但批次未全部决断：持续挂起不返回（修复"信令早退"回归）。"""
        async def _case():
            ex = _make_executor()
            collect = mock.Mock(return_value=({"r1": {"t1": True}}, False))  # 永不完整
            with self._patch_loop(collect=collect):
                async def trigger():
                    await asyncio.sleep(0.02)
                    ex.on_approval_signal({"graph_interrupt_id": "g1"})
                t = asyncio.create_task(trigger())
                # 挂起等待内部循环永不退出 → wait_for 超时抛 TimeoutError
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(ex._wait_for_batch_decision("g1"), timeout=0.15)
                await t
            # 信令后确实重新读取过 DB（≥2 次 collect）
            assert collect.call_count >= 2

        asyncio.run(_case())

    def test_completes_after_full_resolution(self):
        """逐步决断：全部决断后才恢复，返回完整 decisions。"""
        async def _case():
            ex = _make_executor()
            steps = [
                (None, False),  # 首次：未决断
                ({"r1": {"t1": True}}, False),  # 信令唤醒后：部分决断（仍不完整）
                ({"r1": {"t1": True, "t2": False}}, True),  # 全部决断
            ]
            collect = mock.Mock(side_effect=steps)
            fin = mock.Mock()
            with self._patch_loop(collect=collect, finalize=fin):
                async def trigger():
                    await asyncio.sleep(0.03)
                    ex.on_approval_signal({"graph_interrupt_id": "g1"})
                    await asyncio.sleep(0.05)
                    ex.on_approval_signal({"graph_interrupt_id": "g1"})
                t = asyncio.create_task(trigger())
                decisions = await ex._wait_for_batch_decision("g1")
                await t
            assert decisions == {"r1": {"t1": True, "t2": False}}
            assert collect.call_count >= 3  # 期间至少重新校验过 DB
            fin.assert_called_once()

        asyncio.run(_case())

    def test_heal_called_while_pending(self):
        """批次未决断时周期性自愈过期审批（执行器内建超时处理）。"""
        async def _case():
            ex = _make_executor()
            heal = mock.Mock()
            with self._patch_loop(
                collect=mock.Mock(return_value=(None, False)),
                heal=heal,
                poll=0.01,
            ):
                async def trigger():
                    await asyncio.sleep(0.05)
                    ex.on_approval_signal({"graph_interrupt_id": "g1"})
                t = asyncio.create_task(trigger())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(ex._wait_for_batch_decision("g1"), timeout=0.12)
                await t
            heal.assert_called()

        asyncio.run(_case())

    def test_signal_wakes_only_matching_batch(self):
        """on_approval_signal 只唤醒对应批次的挂起协程。"""
        async def _case():
            ex = _make_executor()
            collect = mock.Mock(return_value=(None, False))
            with self._patch_loop(collect=collect):
                async def trigger():
                    await asyncio.sleep(0.02)
                    ex.on_approval_signal({"graph_interrupt_id": "g2"})  # 错误批次：不应唤醒
                    await asyncio.sleep(0.02)
                    ex.on_approval_signal({"graph_interrupt_id": "g1"})  # 正确批次
                t = asyncio.create_task(trigger())
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(ex._wait_for_batch_decision("g1"), timeout=0.1)
                await t
            # g1 的 wait 期间 collect 多次（g2 信令未唤醒 g1，g1 信令唤醒后重新校验）
            assert collect.call_count >= 2

        asyncio.run(_case())


class TestWaitForInitialBatch(unittest.TestCase):
    """恢复模式：服务重启后等待首个已决断批次。"""

    def test_no_batches_returns_empty(self):
        """任务无审批批次：返回空 dict，agent 从 checkpoint 自然继续。"""
        async def _case():
            ex = _make_executor()
            with mock.patch.object(ex, "_list_batch_ids", return_value=[]):
                decisions = await ex._wait_for_initial_batch()
            assert decisions == {}

        asyncio.run(_case())

    def test_resolved_batch_returns_decisions(self):
        """存在已决断批次：终态化并返回 decisions。"""
        async def _case():
            ex = _make_executor()
            full = {"r1": {"t1": True}}
            fin = mock.Mock()
            with mock.patch.object(ex, "_list_batch_ids", return_value=["g1"]), mock.patch(
                f"{_MODULE}.collect_batch_decisions", return_value=(full, True)
            ), mock.patch(f"{_MODULE}.self_heal_expired_approvals"), mock.patch(
                f"{_MODULE}.finalize_batch_approvals", fin
            ):
                decisions = await ex._wait_for_initial_batch()
            assert decisions == full
            fin.assert_called_once_with(full, "deep_research", "t1")

        asyncio.run(_case())


class TestRunCleanup(unittest.TestCase):
    """run() 生命周期：成功/失败/取消后均释放 checkpointer 并移除会话槽位。"""

    def _patch_run_deps(self):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(mock.patch(f"{_MODULE}._update_task_status", new_callable=mock.AsyncMock))
        stack.enter_context(
            mock.patch.object(SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock)
        )
        stack.enter_context(
            mock.patch.object(
                SessionExecutor,
                "_build_agent",
                new_callable=mock.AsyncMock,
                return_value=mock.Mock(),
            )
        )
        stack.enter_context(mock.patch.object(SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock))
        return stack

    def test_success_cleans_up(self):
        async def _case():
            ex = _make_executor()
            handle = mock.patch.object(SessionExecutor, "_handle_result", new_callable=mock.AsyncMock)
            with self._patch_run_deps(), handle, mock.patch(
                f"{_MODULE}.execute_research_async", new_callable=mock.AsyncMock, return_value=_result()
            ):
                await ex.run()
            ex.manager.remove_session.assert_called_once_with("t1")

        asyncio.run(_case())

    def test_failure_cleans_up(self):
        async def _case():
            ex = _make_executor()
            with self._patch_run_deps(), mock.patch.object(
                SessionExecutor, "_handle_failure", new_callable=mock.AsyncMock
            ) as handle, mock.patch(
                f"{_MODULE}.execute_research_async", new_callable=mock.AsyncMock, side_effect=RuntimeError("boom")
            ):
                await ex.run()
            handle.assert_awaited_once()
            ex.manager.remove_session.assert_called_once_with("t1")

        asyncio.run(_case())

    def test_cancel_cleans_up(self):
        async def _case():
            ex = _make_executor()

            async def _never_returns(*args, **kwargs):
                await asyncio.Event().wait()  # 永不返回，等待 cancel

            with mock.patch(f"{_MODULE}._update_task_status", new_callable=mock.AsyncMock), mock.patch.object(
                SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock
            ), mock.patch.object(
                SessionExecutor, "_build_agent", new_callable=mock.AsyncMock, return_value=mock.Mock()
            ), mock.patch(
                f"{_MODULE}.execute_research_async", side_effect=_never_returns
            ), mock.patch.object(
                SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock
            ):
                task = asyncio.create_task(ex.run())
                await asyncio.sleep(0.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            ex.manager.remove_session.assert_called_once_with("t1")

        asyncio.run(_case())


class TestSessionManager(unittest.TestCase):
    """会话注册表：幂等启动 / 并发限流 / 移除幂等 / 启动恢复。"""

    def _make_manager(self, max_concurrency=5):
        return SessionManager(max_concurrency=max_concurrency)

    def test_start_session_idempotent(self):
        async def _case():
            manager = self._make_manager()
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls:
                payload = {"thread_id": "t1", "query": "q", "user_id": 1, "session_id": "s1"}
                await manager.start_session(payload)
                await manager.start_session(payload)  # 重复启动被忽略
            assert ex_cls.call_count == 1

        asyncio.run(_case())

    def test_concurrency_limit(self):
        async def _case():
            manager = self._make_manager(max_concurrency=1)
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls:
                await manager.start_session({"thread_id": "t1", "query": "q"})
                await manager.start_session({"thread_id": "t2", "query": "q"})
            assert ex_cls.call_count == 1  # 第二个超限被拒

        asyncio.run(_case())

    def test_remove_session_idempotent(self):
        manager = self._make_manager()
        manager.remove_session("nope")  # 不存在的会话：无异常
        manager._sessions["t1"] = object()
        manager.remove_session("t1")
        assert "t1" not in manager._sessions
        manager.remove_session("t1")  # 再次移除：幂等

    def test_recover_unfinished(self):
        async def _case():
            manager = self._make_manager()
            task = mock.Mock(
                task_id="t1",
                query="q",
                created_by_id=1,
                session_id="s1",
                enable_web_search=True,
                enable_doc_analysis=False,
                knowledge_base_ids=[],
                use_mcp=False,
                selected_mcp_servers=[],
                selected_tools=[],
                model="gpt-4o",
            )
            qs = mock.Mock()
            qs.filter.return_value = qs
            qs.__iter__ = lambda self: iter([task])
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls, mock.patch("Django_xm.apps.research.models.ResearchTask.objects", qs):
                await manager.recover_unfinished()
            assert ex_cls.call_count == 1
            kwargs = ex_cls.call_args.kwargs
            assert kwargs["thread_id"] == "t1"
            assert kwargs["params"]["resume_mode"] is True

        asyncio.run(_case())


class TestRetrySubagentInjection(unittest.TestCase):
    """子代理重试指令注入（Task 3）：运行中会话入队 / 协程结束拒绝 / 恢复预注入。"""

    def test_injects_to_running_session(self):
        """运行中会话：on_retry_subagent 入队成功，指令可被消费。"""
        async def _case():
            ex = _make_executor()
            pending = asyncio.create_task(asyncio.sleep(10))
            ex._run_task = pending
            try:
                ok = ex.on_retry_subagent(
                    {"agent_path": ["main", "web-researcher"], "tool_call_id": "c1"}
                )
                assert ok is True
                drained = ex._retry_queue.get_nowait()
                assert drained["tool_call_id"] == "c1"
                assert ex._retry_queue.empty()
            finally:
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending

        asyncio.run(_case())

    def test_rejects_finished_run_task(self):
        """执行协程已结束：拒绝注入（调用方转入恢复路径）。"""
        async def _case():
            ex = _make_executor()
            done = asyncio.create_task(asyncio.sleep(0))
            await done
            ex._run_task = done
            assert ex.on_retry_subagent({"tool_call_id": "c1"}) is False
            assert ex._retry_queue.empty()

        asyncio.run(_case())

    def test_rejects_without_run_task(self):
        """执行协程尚未启动：拒绝注入。"""
        ex = _make_executor()
        assert ex.on_retry_subagent({"tool_call_id": "c1"}) is False

    def test_run_pre_injects_retry_instruction(self):
        """恢复重试场景：params.retry_instruction 在 run() 中预注入队列并挂接 adapter。"""
        async def _case():
            ex = _make_executor(
                params={
                    "retry_instruction": {
                        "agent_path": ["main", "web-researcher"],
                        "tool_call_id": "c1",
                    }
                }
            )
            agent = mock.Mock()
            with mock.patch(f"{_MODULE}._update_task_status", new_callable=mock.AsyncMock), mock.patch.object(
                SessionExecutor, "_publish_status_change", new_callable=mock.AsyncMock
            ), mock.patch.object(
                ex, "_build_agent", new_callable=mock.AsyncMock, return_value=agent
            ), mock.patch.object(ex, "_release_checkpointer", new_callable=mock.AsyncMock), mock.patch.object(
                ex, "_handle_result", new_callable=mock.AsyncMock
            ), mock.patch(
                f"{_MODULE}.execute_research_async", new_callable=mock.AsyncMock, return_value=_result()
            ):
                await ex.run()
            assert agent._retry_instruction_queue is ex._retry_queue
            drained = ex._retry_queue.get_nowait()
            assert drained["tool_call_id"] == "c1"

        asyncio.run(_case())


class TestSessionManagerRetryRecovery(unittest.TestCase):
    """重试信令路由：运行中注入 / 无会话恢复 / 已完成任务拒绝。"""

    def _make_manager(self, max_concurrency=5):
        return SessionManager(max_concurrency=max_concurrency)

    def _make_task(self, status="failed"):
        return mock.Mock(
            task_id="t1",
            query="q",
            created_by_id=1,
            session_id=None,
            enable_web_search=True,
            enable_doc_analysis=False,
            knowledge_base_ids=[],
            use_mcp=False,
            selected_mcp_servers=[],
            selected_tools=[],
            model="gpt-4o",
            status=status,
        )

    def test_routes_to_running_executor(self):
        """运行中会话：重试信令直接路由到执行器注入。"""
        async def _case():
            manager = self._make_manager()
            ex = mock.Mock()
            ex.on_retry_subagent.return_value = True
            manager._sessions["t1"] = ex
            await manager.on_retry_subagent_signal("t1", {"tool_call_id": "c1"})
            ex.on_retry_subagent.assert_called_once_with({"tool_call_id": "c1"})

        asyncio.run(_case())

    def test_recovers_session_with_retry_instruction(self):
        """会话不在内存（已结束/服务重启）：从 checkpoint 恢复并预置重试指令。"""
        async def _case():
            manager = self._make_manager()
            task = self._make_task(status="failed")
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls, mock.patch(
                "Django_xm.apps.research.models.ResearchTask.objects.filter"
            ) as filt:
                filt.return_value.first.return_value = task
                await manager.on_retry_subagent_signal("t1", {"tool_call_id": "c1"})
            assert ex_cls.call_count == 1
            kwargs = ex_cls.call_args.kwargs
            assert kwargs["thread_id"] == "t1"
            assert kwargs["params"]["resume_mode"] is True
            assert kwargs["params"]["retry_instruction"] == {"tool_call_id": "c1"}

        asyncio.run(_case())

    def test_skips_recovery_for_completed_task(self):
        """任务已完成：无法重试，不启动恢复会话。"""
        async def _case():
            manager = self._make_manager()
            task = self._make_task(status="completed")
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls, mock.patch(
                "Django_xm.apps.research.models.ResearchTask.objects.filter"
            ) as filt:
                filt.return_value.first.return_value = task
                await manager.on_retry_subagent_signal("t1", {"tool_call_id": "c1"})
            ex_cls.assert_not_called()

        asyncio.run(_case())

    def test_recovers_after_stale_slot_released(self):
        """旧会话槽位未释放（协程结束清理竞态）：等待释放后仍完成恢复。"""
        async def _case():
            manager = self._make_manager()
            stale = mock.Mock()
            stale.on_retry_subagent.return_value = False  # 模拟协程已结束，无法注入
            manager._sessions["t1"] = stale
            task = self._make_task(status="failed")

            async def _release():
                await asyncio.sleep(0.02)
                manager.remove_session("t1")

            release_task = asyncio.create_task(_release())
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls, mock.patch(
                "Django_xm.apps.research.models.ResearchTask.objects.filter"
            ) as filt:
                filt.return_value.first.return_value = task
                await manager.on_retry_subagent_signal("t1", {"tool_call_id": "c1"})
            await release_task
            assert ex_cls.call_count == 1

        asyncio.run(_case())


class TestResearchResumeBaseline(unittest.TestCase):
    """服务重启恢复基线：从 ResearchTask 重建子代理状态并注入新建 adapter。

    覆盖 spec「服务重启恢复场景子代理工具条目丢失」修复：
    - ``_load_research_resume_baseline`` 从 ``ResearchTask.tool_calls`` 筛选
      ``subagent_thread_id`` 非空条目重建（key=tool_call_id），主代理工具
      （无图层字段）被过滤；
    - ``_inject_research_resume_baseline`` 将基线注入新建 adapter 的空内存态。
    """

    def test_load_research_resume_baseline_filters_subagent_entries(self):
        """仅 subagent_thread_id 非空条目进入基线，key=tool_call_id，图层字段保留。"""
        task = mock.Mock(
            tool_calls=[
                {
                    "id": "c1",
                    "name": "shell_exec",
                    "subagent_thread_id": "sa1",
                    "position": 3,
                    "status": "completed",
                    "result": {"output": "ok"},
                },
                {"id": "c2", "name": "web_search", "subagent_thread_id": "", "position": 10},
                {"id": "c3", "name": "shell_exec", "subagent_thread_id": "sa2", "depth": 2},
                {"id": "c4", "name": "write_file"},
            ],
            subagent_contents={
                "sa1": {"content": "a", "reasoning_content": "r"},
                "sa2": {"content": "b"},
            },
        )
        qs = mock.Mock()
        qs.filter.return_value = qs
        qs.first.return_value = task
        with mock.patch("Django_xm.apps.research.models.ResearchTask.objects", qs):
            baseline = SessionExecutor._load_research_resume_baseline("t1")

        self.assertEqual(set(baseline["subagent_tool_entries"].keys()), {"c1", "c3"})
        self.assertEqual(baseline["subagent_tool_entries"]["c1"]["subagent_thread_id"], "sa1")
        self.assertEqual(baseline["subagent_tool_entries"]["c1"]["position"], 3)
        self.assertEqual(baseline["subagent_tool_entries"]["c1"]["status"], "completed")
        self.assertEqual(baseline["subagent_tool_entries"]["c1"]["result"], {"output": "ok"})
        self.assertEqual(
            baseline["subagent_contents"],
            {"sa1": {"content": "a", "reasoning_content": "r"}, "sa2": {"content": "b"}},
        )

    def test_load_research_resume_baseline_task_missing(self):
        """任务不存在 → 空基线（恢复注入直接跳过）。"""
        qs = mock.Mock()
        qs.filter.return_value = qs
        qs.first.return_value = None
        with mock.patch("Django_xm.apps.research.models.ResearchTask.objects", qs):
            baseline = SessionExecutor._load_research_resume_baseline("t1")
        self.assertEqual(baseline, {})

    def test_inject_research_resume_baseline_fills_empty_adapter(self):
        """基线注入：新建 adapter 的空内存态被 DB 基线填充（key 按 tool_call_id）。"""
        async def _case():
            ex = _make_executor()
            agent = mock.Mock()
            agent.subagent_contents = {}
            agent._subagent_tool_entries = {}
            ex._agent = agent
            baseline = {
                "subagent_contents": {"sa1": {"content": "a"}},
                "subagent_tool_entries": {
                    "c1": {"id": "c1", "subagent_thread_id": "sa1", "position": 3}
                },
            }
            with mock.patch.object(ex, "_load_research_resume_baseline", return_value=baseline):
                await ex._inject_research_resume_baseline()

            self.assertEqual(agent.subagent_contents, {"sa1": {"content": "a"}})
            self.assertEqual(
                agent._subagent_tool_entries,
                {"c1": {"id": "c1", "subagent_thread_id": "sa1", "position": 3}},
            )

        asyncio.run(_case())

    def test_inject_research_resume_baseline_merges_existing(self):
        """基线注入合并而非覆盖：adapter 已有（恢复轮新产生）条目保留。"""
        async def _case():
            ex = _make_executor()
            agent = mock.Mock()
            agent.subagent_contents = {"sa2": {"content": "new"}}
            agent._subagent_tool_entries = {
                "c9": {"id": "c9", "subagent_thread_id": "sa2", "position": 0}
            }
            ex._agent = agent
            baseline = {
                "subagent_contents": {"sa1": {"content": "a"}},
                "subagent_tool_entries": {
                    "c1": {"id": "c1", "subagent_thread_id": "sa1", "position": 3}
                },
            }
            with mock.patch.object(ex, "_load_research_resume_baseline", return_value=baseline):
                await ex._inject_research_resume_baseline()

            self.assertEqual(agent.subagent_contents, {"sa2": {"content": "new"}, "sa1": {"content": "a"}})
            self.assertEqual(
                set(agent._subagent_tool_entries.keys()), {"c9", "c1"}
            )

        asyncio.run(_case())

    def test_inject_research_resume_baseline_without_agent(self):
        """_agent 未构建（异常路径）：注入安全跳过。"""
        async def _case():
            ex = _make_executor()
            ex._agent = None
            with mock.patch.object(
                ex, "_load_research_resume_baseline", return_value={"subagent_contents": {}, "subagent_tool_entries": {}}
            ):
                await ex._inject_research_resume_baseline()  # 不应抛异常

        asyncio.run(_case())


if __name__ == "__main__":
    unittest.main()
