"""SessionExecutor / SessionManager 单元测试（Task 8 / 9.1）。

覆盖：审批挂起与信令唤醒（批次完整性）、超时自愈、协程结束清理（成功/失败/取消）、
启动恢复（recover_unfinished）、会话注册/并发限流/移除幂等、on_approval_signal 唤醒正确批次。

隔离策略：测试不触碰真实 DB/Redis——collect_batch_decisions / self_heal_expired_approvals /
finalize_batch_approvals / execute_research_async 等外部依赖全部 mock，仅验证执行器编排逻辑。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_session_executor --noinput
    （⚠️ 禁止 python -m unittest 直跑：绕过 Django 测试运行器会连接真实 dev 库，
    无测试库隔离，已发生过测试数据污染，见 .trae/memory/2026-10-05.md 第五批教训）
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
from Django_xm.services.fastapi_service.session_manager import SessionManager

_MODULE = "Django_xm.services.fastapi_service.session_executor"
# B⑦ 收敛后 _wait_for_batch_decision 薄委托到 approval_batch.await_wait_for_batch_decision，
# 其依赖（collect/self_heal/finalize）需 patch 在 approval_batch 模块命名空间
_AB_MODULE = "Django_xm.apps.approvals.services.approval_batch"


def _make_executor(thread_id="t1", **kw):
    # E7：SessionExecutor 经 __new__ 工厂按 session_type 分派域子类
    # （chat → ChatSessionExecutor，其余 → ResearchSessionExecutor），
    # 域专属方法（_run_chat/_wait_for_chat_initial_batch 等）仅存在于
    # 对应子类，chat 域用例须以 session_type="chat" 构造。
    manager = mock.Mock()
    return SessionExecutor(
        manager,
        thread_id=thread_id,
        query=kw.get("query", "q"),
        user_id=kw.get("user_id", 1),
        session_id=kw.get("session_id", "s1"),
        message_id=kw.get("message_id", "m1"),
        params=kw.get("params", {}),
        session_type=kw.get("session_type", "research"),
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
        """patch 共享等待循环的依赖：collect/self_heal/finalize 打在 approval_batch
        模块（B⑦ 收敛后共享实现所在命名空间），_BATCH_POLL_INTERVAL 仍打在
        session_executor 模块（薄委托读取模块常量传入 poll_interval）。"""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            mock.patch.multiple(
                _AB_MODULE,
                collect_batch_decisions=collect if collect is not None else mock.DEFAULT,
                self_heal_expired_approvals=heal if heal is not None else mock.DEFAULT,
                finalize_batch_approvals=finalize if finalize is not None else mock.DEFAULT,
            )
        )
        stack.enter_context(mock.patch(f"{_MODULE}._BATCH_POLL_INTERVAL", poll))
        return stack

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
        """任务无审批批次：返回空 dict（调用方按 B5/F3 安全终态处理，不重入）。"""
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

    def test_failure_uses_classified_message_not_raw_exception(self):
        """run() 异常 → _handle_failure 收到分类文案 + error_code，非裸 str(exc)（整改 A1.4）。"""
        async def _case():
            ex = _make_executor()
            with self._patch_run_deps(), mock.patch.object(
                SessionExecutor, "_handle_failure", new_callable=mock.AsyncMock
            ) as handle, mock.patch(
                f"{_MODULE}.execute_research_async",
                new_callable=mock.AsyncMock,
                side_effect=TimeoutError("POST https://api.example.com/v1 timed out"),
            ):
                await ex.run()
            handle.assert_awaited_once_with("请求超时，请稍后重试", error_code="MODEL_CALL_ERROR")

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

    def setUp(self):
        # B4：recover_unfinished 现附带 chat 审批挂起扫描（真实 DB 查询）。
        # 本类全 mock 隔离——统一 patch Approval.objects（空结果=不重建会话），
        # 防 sync_to_async 工作线程打开真实连接泄漏到 teardown（曾致
        # "test_langchain is being accessed by other users"）。
        p = mock.patch("Django_xm.apps.approvals.models.Approval.objects")
        approval_objects = p.start()
        approval_objects.filter.return_value.order_by.return_value = []
        self.addCleanup(p.stop)
        # B4 勘误后 _scan 追加 ChatSession 存在性查询（孤儿审批收编），同上隔离
        p_chat = mock.patch("Django_xm.apps.chat.models.ChatSession.objects")
        chat_objects = p_chat.start()
        chat_objects.filter.return_value.values_list.return_value = []
        self.addCleanup(p_chat.stop)
        # C1：start_session 取消竞态防护含真实 DB 查询（ResearchTask.status），
        # 同上全 mock 隔离（防护专项测试见 test_research_cancel.py）
        p2 = mock.patch.object(
            SessionManager, "_is_research_task_cancelled", new_callable=mock.AsyncMock, return_value=False
        )
        p2.start()
        self.addCleanup(p2.stop)

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

    # ── B5：PENDING 超时补偿（start 信令丢失→自愈）──────────────────

    @staticmethod
    def _make_pending_task(task_id="t-pending"):
        from django.utils import timezone

        return mock.Mock(
            task_id=task_id,
            query="研究量子计算",
            created_by_id=7,
            session_id="s9",
            enable_web_search=False,
            enable_doc_analysis=False,
            enable_sandbox=False,
            knowledge_base_ids=[],
            use_mcp=False,
            selected_mcp_servers=[],
            selected_tools=[],
            model="deepseek-chat",
            created_at=timezone.now(),
        )

    def test_recover_pending_stale_compensated_fresh_start(self):
        """信令丢失自愈：PENDING 超时任务以全新启动补偿（不带 resume_mode）。"""
        async def _case():
            manager = self._make_manager()
            pending_task = self._make_pending_task()
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls, mock.patch(
                "Django_xm.apps.research.models.ResearchTask.objects"
            ) as objects:
                # 第一次 filter（active）返回空，第二次 filter（stale pending）返回任务
                objects.filter.side_effect = [[], [pending_task]]
                await manager.recover_unfinished()
            assert ex_cls.call_count == 1
            params = ex_cls.call_args.kwargs["params"]
            assert params["thread_id"] == "t-pending"
            assert params["query"] == "研究量子计算"
            # 从未执行的任务无 checkpoint：必须全新启动，不得走 resume 路径
            assert "resume_mode" not in params

        asyncio.run(_case())

    def test_recover_pending_threshold_aligned_query(self):
        """补偿扫描按 PENDING + created_at<now-5min 口径过滤（阈值可传参覆盖）。"""
        from datetime import timedelta

        from django.utils import timezone

        from Django_xm.apps.research.models import ResearchTaskStatus

        async def _case():
            manager = self._make_manager()
            filter_calls = []
            returns = [[], []]

            def _fake_filter(**kwargs):
                filter_calls.append(kwargs)
                return returns.pop(0)

            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ), mock.patch(
                "Django_xm.apps.research.models.ResearchTask.objects"
            ) as objects:
                objects.filter.side_effect = _fake_filter
                await manager.recover_unfinished(pending_timeout_minutes=2)

            assert filter_calls[0]["status__in"] == [
                ResearchTaskStatus.RUNNING,
                ResearchTaskStatus.AWAITING_APPROVAL,
            ]
            assert filter_calls[1]["status"] == ResearchTaskStatus.PENDING
            cutoff = filter_calls[1]["created_at__lt"]
            # 阈值口径：cutoff ≈ now - 2min（容差 10s 覆盖执行耗时）
            delta = timezone.now() - cutoff
            assert timedelta(minutes=2) <= delta < timedelta(minutes=2, seconds=10)

        asyncio.run(_case())

    def test_recover_active_wins_over_pending_dedup(self):
        """同一任务同时出现在 active 与 pending 扫描（竞态窗口）：只启动一次且按 active 语义。"""
        async def _case():
            manager = self._make_manager()
            task = mock.Mock(
                task_id="t-dup",
                query="q",
                created_by_id=1,
                session_id="s1",
                enable_web_search=True,
                enable_doc_analysis=False,
                enable_sandbox=False,
                knowledge_base_ids=[],
                use_mcp=False,
                selected_mcp_servers=[],
                selected_tools=[],
                model="gpt-4o",
            )
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls, mock.patch(
                "Django_xm.apps.research.models.ResearchTask.objects"
            ) as objects:
                objects.filter.side_effect = [[task], [task]]
                await manager.recover_unfinished()
            assert ex_cls.call_count == 1
            assert ex_cls.call_args.kwargs["params"]["resume_mode"] is True

        asyncio.run(_case())

    def test_recover_skips_in_memory_session(self):
        """迟到信令竞态防护：任务会话已在内存（信令已生效）时不重复补偿。"""
        async def _case():
            manager = self._make_manager()
            pending_task = self._make_pending_task(task_id="t-live")
            manager._sessions["t-live"] = object()  # 会话已激活（如迟到信令先到）
            with mock.patch(
                "Django_xm.services.fastapi_service.session_manager.SessionExecutor"
            ) as ex_cls, mock.patch(
                "Django_xm.apps.research.models.ResearchTask.objects"
            ) as objects:
                objects.filter.side_effect = [[], [pending_task]]
                await manager.recover_unfinished()
            assert ex_cls.call_count == 0

        asyncio.run(_case())

    # ── B6：挂起会话 watchdog（子代理挂死→父会话兜底）────────────────

    def test_suspended_timeout_triggers_finalize(self):
        """挂起超过最大存活时长 → 触发终态化：任务失败态 + 槽位移除。"""
        async def _case():
            ex = _make_executor(thread_id="t-hang")
            # 模拟子代理挂死：会话进入业务等待挂起且已超过存活时长
            ex._suspended = True
            ex._suspended_since = time.monotonic() - 9999
            manager = ex.manager
            with mock.patch.object(
                SessionExecutor, "_handle_failure", new_callable=mock.AsyncMock
            ) as fail_mock, mock.patch.object(
                SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock
            ):
                triggered = ex.enforce_suspended_timeout(7200)
                # 终态化是异步任务：让出事件循环使其完成
                await asyncio.sleep(0)
                await asyncio.gather(*list(ex._background_tasks))
            assert triggered is True
            fail_mock.assert_awaited_once()
            assert "子代理长时间未返回" in fail_mock.await_args.args[0]
            manager.remove_session.assert_called_once_with("t-hang")

        asyncio.run(_case())

    def test_suspended_within_timeout_not_finalized(self):
        """挂起未超时长 → 不触发（合法长等待，如子代理仍在工作）。"""
        ex = _make_executor(thread_id="t-fresh")
        ex._suspended = True
        ex._suspended_since = time.monotonic() - 10
        assert ex.enforce_suspended_timeout(7200) is False
        assert ex._finalized_by_watchdog is False

    def test_active_session_not_finalized(self):
        """非挂起态（活跃执行/未启动）→ watchdog 不干预。"""
        ex = _make_executor(thread_id="t-active")
        assert ex.suspended_since is None
        assert ex.enforce_suspended_timeout(7200) is False

    def test_suspended_finalize_idempotent(self):
        """防重：watchdog 重复扫描不重复触发终态化。"""
        async def _case():
            ex = _make_executor(thread_id="t-dup")
            ex._suspended = True
            ex._suspended_since = time.monotonic() - 9999
            with mock.patch.object(
                SessionExecutor, "_handle_failure", new_callable=mock.AsyncMock
            ), mock.patch.object(
                SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock
            ):
                assert ex.enforce_suspended_timeout(7200) is True
                assert ex.enforce_suspended_timeout(7200) is False  # 已触发，防重
                await asyncio.gather(*list(ex._background_tasks))

        asyncio.run(_case())

    def test_manager_watchdog_sweeps_sessions(self):
        """SessionManager 周期扫描对全部会话执行 watchdog 检查。"""
        manager = SessionManager()
        ex_a = _make_executor(thread_id="t-a")
        ex_b = mock.Mock()
        ex_b.thread_id = "t-b"
        ex_b.enforce_suspended_timeout.return_value = False
        manager._sessions["t-a"] = ex_a
        manager._sessions["t-b"] = ex_b
        manager._enforce_suspended_watchdog()
        ex_b.enforce_suspended_timeout.assert_called_once()
        assert ex_b.enforce_suspended_timeout.call_args.args[0] > 0


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

        self.assertEqual(set(baseline["tool_entries"].keys()), {"c1", "c3"})
        self.assertEqual(baseline["tool_entries"]["c1"]["subagent_thread_id"], "sa1")
        self.assertEqual(baseline["tool_entries"]["c1"]["position"], 3)
        self.assertEqual(baseline["tool_entries"]["c1"]["status"], "completed")
        self.assertEqual(baseline["tool_entries"]["c1"]["result"], {"output": "ok"})
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
            agent._tool_entries = {}
            ex._agent = agent
            baseline = {
                "subagent_contents": {"sa1": {"content": "a"}},
                "tool_entries": {
                    "c1": {"id": "c1", "subagent_thread_id": "sa1", "position": 3}
                },
            }
            with mock.patch.object(ex, "_load_research_resume_baseline", return_value=baseline):
                await ex._inject_research_resume_baseline()

            self.assertEqual(agent.subagent_contents, {"sa1": {"content": "a"}})
            self.assertEqual(
                agent._tool_entries,
                {"c1": {"id": "c1", "subagent_thread_id": "sa1", "position": 3}},
            )

        asyncio.run(_case())

    def test_inject_research_resume_baseline_merges_existing(self):
        """基线注入合并而非覆盖：adapter 已有（恢复轮新产生）条目保留。"""
        async def _case():
            ex = _make_executor()
            agent = mock.Mock()
            agent.subagent_contents = {"sa2": {"content": "new"}}
            agent._tool_entries = {
                "c9": {"id": "c9", "subagent_thread_id": "sa2", "position": 0}
            }
            ex._agent = agent
            baseline = {
                "subagent_contents": {"sa1": {"content": "a"}},
                "tool_entries": {
                    "c1": {"id": "c1", "subagent_thread_id": "sa1", "position": 3}
                },
            }
            with mock.patch.object(ex, "_load_research_resume_baseline", return_value=baseline):
                await ex._inject_research_resume_baseline()

            self.assertEqual(agent.subagent_contents, {"sa2": {"content": "new"}, "sa1": {"content": "a"}})
            self.assertEqual(
                set(agent._tool_entries.keys()), {"c9", "c1"}
            )

        asyncio.run(_case())

    def test_inject_research_resume_baseline_without_agent(self):
        """_agent 未构建（异常路径）：注入安全跳过。"""
        async def _case():
            ex = _make_executor()
            ex._agent = None
            with mock.patch.object(
                ex,
                "_load_research_resume_baseline",
                return_value={
                    "subagent_contents": {},
                    "tool_entries": {},
                },
            ):
                await ex._inject_research_resume_baseline()  # 不应抛异常

        asyncio.run(_case())


class TestResumeModeNoBatchFailSafe(unittest.TestCase):
    """B5/P2-12：research 恢复无批次 → 安全终态（禁止空 HumanMessage 重入）。"""

    def test_resume_without_batches_fails_safe(self):
        """恢复模式且无审批批次：_handle_failure 安全终态，绝不重入执行。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True})
            with TestRunCleanup._patch_run_deps(self), mock.patch.object(
                SessionExecutor,
                "_wait_for_initial_batch",
                new_callable=mock.AsyncMock,
                return_value={},
            ), mock.patch.object(
                SessionExecutor, "_handle_failure", new_callable=mock.AsyncMock
            ) as handle, mock.patch(
                f"{_MODULE}.execute_research_async", new_callable=mock.AsyncMock
            ) as execute:
                await ex.run()
            handle.assert_awaited_once_with(
                "研究任务在服务重启后无法安全恢复（中断点不在审批等待状态），"
                "任务已终止，请重新发起研究"
            )
            execute.assert_not_awaited()

        asyncio.run(_case())

    def test_resume_with_decisions_still_reenters(self):
        """恢复模式有已决断批次：正常重入执行（守卫不误伤既有恢复路径）。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True})
            full = {"r1": {"t1": True}}
            with TestRunCleanup._patch_run_deps(self), mock.patch.object(
                SessionExecutor,
                "_wait_for_initial_batch",
                new_callable=mock.AsyncMock,
                return_value=full,
            ), mock.patch.object(
                SessionExecutor, "_inject_research_resume_baseline", new_callable=mock.AsyncMock
            ), mock.patch.object(
                SessionExecutor, "_handle_result", new_callable=mock.AsyncMock
            ), mock.patch(
                f"{_MODULE}.execute_research_async",
                new_callable=mock.AsyncMock,
                return_value=_result(),
            ) as execute:
                await ex.run()
            execute.assert_awaited_once()

        asyncio.run(_case())


class TestRunChatResumeMode(unittest.TestCase):
    """B4/P2-11：chat 审批挂起跨服务重启恢复（_run_chat 恢复分支）。"""

    def test_resume_with_decisions_reenters(self):
        """等待批次决断 → Command(resume) 注入 params → 基线注入 → 重入执行链。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True}, session_type="chat")
            full = {"r1": {"t1": True}}
            with mock.patch.object(
                ex,
                "_wait_for_chat_initial_batch",
                new_callable=mock.AsyncMock,
                return_value=full,
            ), mock.patch.object(
                ex, "_inject_chat_resume_baseline", new_callable=mock.AsyncMock
            ) as inject, mock.patch(
                "Django_xm.services.fastapi_service.chat_executor_core.run_chat_session",
                new_callable=mock.AsyncMock,
            ) as run:
                await ex._run_chat()
            assert ex.params["resume_command"].resume == full
            inject.assert_awaited_once()
            run.assert_awaited_once_with(ex, ex.params)

        asyncio.run(_case())

    def test_resume_without_batch_aborts_with_failure_broadcast(self):
        """无批次（扫描后行间消失）：广播失败终态，不重入执行链。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True}, message_id="m9", session_type="chat")
            with mock.patch.object(
                ex,
                "_wait_for_chat_initial_batch",
                new_callable=mock.AsyncMock,
                return_value={},
            ), mock.patch(
                "Django_xm.common.realtime_events.publish_event", new_callable=mock.AsyncMock
            ) as pub, mock.patch(
                "Django_xm.services.fastapi_service.chat_executor_core.run_chat_session",
                new_callable=mock.AsyncMock,
            ) as run:
                await ex._run_chat()
            pub.assert_awaited_once()
            payload = pub.await_args.args[1]
            assert payload["source_id"] == "t1"
            assert payload["message_id"] == "m9"
            assert payload["data"]["success"] is False
            assert "恢复失败" in payload["data"]["error"]
            run.assert_not_awaited()

        asyncio.run(_case())

    def test_resume_stopped_during_wait_broadcasts_stopped_not_failure(self):
        """E10：恢复等待窗口用户停止 → 广播 stopped 终态（非失败终态）+ 遗留批次收尾，不重入执行链。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True}, message_id="m9", session_type="chat")
            with mock.patch.object(
                ex,
                "_wait_for_chat_initial_batch",
                new_callable=mock.AsyncMock,
                return_value=None,
            ), mock.patch(
                "Django_xm.common.realtime_events.publish_event", new_callable=mock.AsyncMock
            ) as pub, mock.patch(
                f"{_MODULE}.abandon_session_pending_approvals"
            ) as abandon, mock.patch(
                "Django_xm.services.fastapi_service.chat_executor_core.run_chat_session",
                new_callable=mock.AsyncMock,
            ) as run:
                await ex._run_chat()
            pub.assert_awaited_once()
            payload = pub.await_args.args[1]
            assert payload["source_id"] == "t1"
            assert payload["message_id"] == "m9"
            # 用户主动停止=优雅停止语义（对齐 _finalize_chat_session 停止分支），非失败
            assert payload["data"]["success"] is True
            assert payload["data"]["stopped"] is True
            assert payload["data"]["incomplete"] is False
            # 遗留 PENDING 批次收尾（TIMEOUT 语义），防 _recover_chat_approvals 复活已停止会话
            abandon.assert_called_once_with("chat", "t1")
            run.assert_not_awaited()

        asyncio.run(_case())

    def test_normal_mode_skips_resume_branch(self):
        """非恢复模式（回归守卫）：不等待批次、不注入 resume_command。"""
        async def _case():
            ex = _make_executor(params={}, session_type="chat")
            with mock.patch(
                "Django_xm.services.fastapi_service.chat_executor_core.run_chat_session",
                new_callable=mock.AsyncMock,
            ) as run:
                await ex._run_chat()
            run.assert_awaited_once_with(ex, ex.params)
            assert "resume_command" not in ex.params

        asyncio.run(_case())


class TestChatInitialBatchWait(unittest.TestCase):
    """B4：_wait_for_chat_initial_batch（chat 版恢复等待，与 research 版同构）。"""

    def test_no_batches_returns_empty(self):
        async def _case():
            ex = _make_executor(session_type="chat")
            with mock.patch.object(ex, "_list_chat_batch_ids", return_value=[]):
                decisions = await ex._wait_for_chat_initial_batch()
            assert decisions == {}

        asyncio.run(_case())

    def test_resolved_batch_returns_decisions_and_finalizes(self):
        async def _case():
            ex = _make_executor(session_type="chat")
            full = {"r1": {"t1": True}}
            fin = mock.Mock()
            with mock.patch.object(ex, "_list_chat_batch_ids", return_value=["g1"]), mock.patch(
                f"{_MODULE}.collect_batch_decisions", return_value=(full, True)
            ), mock.patch(f"{_MODULE}.self_heal_expired_approvals"), mock.patch(
                f"{_MODULE}.finalize_batch_approvals", fin
            ):
                decisions = await ex._wait_for_chat_initial_batch()
            assert decisions == full
            fin.assert_called_once_with(full, "chat", "t1")

        asyncio.run(_case())

    def test_list_chat_batch_ids_dedup_newest_first_and_filters(self):
        """批次 newest-first 去重 + 过滤空批次 ID（DB 查询形状验证）。"""
        rows = ["g1", "g2", "g1", "", "g2"]
        qs = mock.Mock()
        qs.filter.return_value.exclude.return_value.order_by.return_value.values_list.return_value = rows
        with mock.patch("Django_xm.apps.approvals.models.Approval.objects", qs):
            ids = SessionExecutor._list_chat_batch_ids("s1")
        # order_by(-created_at) 最新在前：去重保序后 g1 在前；空 ID 过滤
        assert ids == ["g1", "g2"]

    def test_stop_before_poll_returns_none_without_db_scan(self):
        """E10：停止标志先置位 → 首轮循环顶部检查点退出（不做 DB 扫描/决断收集）。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True}, session_type="chat")
            ex._stop_requested = True
            with mock.patch.object(ex, "_list_chat_batch_ids") as list_ids, mock.patch(
                f"{_MODULE}.collect_batch_decisions"
            ) as collect:
                result = await ex._wait_for_chat_initial_batch()
            assert result is None
            list_ids.assert_not_called()
            collect.assert_not_called()

        asyncio.run(_case())

    def test_stop_before_event_wait_returns_none(self):
        """E10：Event 等待前检查点——DB 扫描/自愈期间到达的停止请求立即退出（不进第二轮）。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True}, session_type="chat")
            calls = {"collect": 0}

            def _fake_collect(source, session_id, gid):
                calls["collect"] += 1
                return {}, False

            def _fake_heal(source, session_id, gid):
                ex._stop_requested = True  # 首轮自愈阶段用户停止

            with mock.patch.object(
                ex, "_list_chat_batch_ids", return_value=["g1"]
            ) as list_ids, mock.patch(
                f"{_MODULE}.collect_batch_decisions", side_effect=_fake_collect
            ), mock.patch(
                f"{_MODULE}.self_heal_expired_approvals", side_effect=_fake_heal
            ), mock.patch(
                f"{_MODULE}.finalize_batch_approvals"
            ) as fin:
                result = await ex._wait_for_chat_initial_batch()
            assert result is None
            # 等待前退出：仅首轮扫描（若只有循环顶部检查点，会先等满轮询间隔再进第二轮）
            assert list_ids.call_count == 1
            assert calls["collect"] == 1
            fin.assert_not_called()  # 未决断批次不得被 finalize

        asyncio.run(_case())

    def test_stop_during_event_wait_wakes_and_returns_none(self):
        """E10：Event 等待期间收到停止请求 → stop() 批量 set 唤醒 → 下轮循环顶部检查退出。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True}, session_type="chat")
            loop = asyncio.get_running_loop()

            def _stop_later():
                # 进入 Event 等待后触发真实 stop() 唤醒路径（批量 set 已注册
                # Event + 置停止标志；finalize_cancel=False：仅验证等待循环的
                # 停止收敛，不触碰终态化级联）
                ex.stop(finalize_cancel=False)

            # 首轮扫描（~ms 级）完成后、轮询间隔（5s）内触发
            loop.call_later(0.2, _stop_later)
            with mock.patch.object(ex, "_list_chat_batch_ids", return_value=["g1"]), mock.patch(
                f"{_MODULE}.collect_batch_decisions", return_value=({}, False)
            ), mock.patch(f"{_MODULE}.self_heal_expired_approvals"):
                result = await ex._wait_for_chat_initial_batch()
            assert result is None
            assert ex.check_stop_requested() is True

        asyncio.run(_case())

    def test_unresolved_batch_polls_until_resolved_without_stop(self):
        """E10 回归守卫：未停止时轮询行为不变（未决断 → Event 等待超时 → 下轮取到决断）。"""
        async def _case():
            ex = _make_executor(params={"resume_mode": True}, session_type="chat")
            full = {"r1": {"t1": True}}
            outcomes = [({}, False), (full, True)]
            with mock.patch.object(ex, "_list_chat_batch_ids", return_value=["g1"]), mock.patch(
                f"{_MODULE}.collect_batch_decisions", side_effect=lambda *a: outcomes.pop(0)
            ), mock.patch(f"{_MODULE}.self_heal_expired_approvals"), mock.patch(
                f"{_MODULE}._BATCH_POLL_INTERVAL", 0.01
            ), mock.patch(
                f"{_MODULE}.finalize_batch_approvals"
            ) as fin:
                decisions = await ex._wait_for_chat_initial_batch()
            assert decisions == full
            fin.assert_called_once_with(full, "chat", "t1")

        asyncio.run(_case())


class TestRecoverChatApprovals(unittest.TestCase):
    """B4：SessionManager._recover_chat_approvals 扫描与重建。"""

    def test_rebuilds_session_from_approval_extra(self):
        """审批行 → resume_mode payload（session_type=chat + extra 配置展开）。"""
        async def _case():
            mgr = SessionManager(max_concurrency=5)
            rows = [
                SimpleNamespace(
                    source_id="sess-1",
                    user_id=7,
                    extra={
                        "graph_interrupt_id": "g1",
                        "message_id": "42",
                        "tool_config": {"use_web_search": True},
                        "model_config": {"model_name": "deepseek-chat"},
                    },
                ),
            ]
            qs = mock.Mock()
            qs.filter.return_value.order_by.return_value = rows
            with mock.patch(
                "Django_xm.apps.approvals.models.Approval.objects", qs
            ), mock.patch(
                "Django_xm.apps.chat.models.ChatSession.objects"
            ) as chat_objs, mock.patch.object(
                mgr, "start_session", new_callable=mock.AsyncMock
            ) as start:
                chat_objs.filter.return_value.values_list.return_value = ["sess-1"]
                await mgr._recover_chat_approvals()
            start.assert_awaited_once()
            payload = start.await_args.args[0]
            assert payload["session_type"] == "chat"
            assert payload["resume_mode"] is True
            assert payload["thread_id"] == "sess-1"
            assert payload["session_id"] == "sess-1"
            assert payload["message_id"] == "42"
            assert payload["user_id"] == 7
            assert payload["use_web_search"] is True
            assert payload["model_name"] == "deepseek-chat"
            # B4 勘误：恢复 payload 必须满足 chat_service 硬索引契约
            assert payload["mode"] == "agent"
            assert payload["message"] == ""

        asyncio.run(_case())

    def test_skips_subagent_rows_empty_batch_and_in_memory_sessions(self):
        """子代理行/无批次行/内存存活会话均跳过。"""
        async def _case():
            mgr = SessionManager(max_concurrency=5)
            mgr._sessions["sess-live"] = object()
            rows = [
                SimpleNamespace(
                    source_id="sess-sub",
                    user_id=1,
                    extra={"subagent_thread_id": "sub-1", "graph_interrupt_id": "g0", "message_id": "1"},
                ),
                SimpleNamespace(
                    source_id="sess-live", user_id=1, extra={"graph_interrupt_id": "g1", "message_id": "2"}
                ),
                SimpleNamespace(source_id="sess-empty", user_id=1, extra={"message_id": "3"}),
            ]
            qs = mock.Mock()
            qs.filter.return_value.order_by.return_value = rows
            with mock.patch(
                "Django_xm.apps.approvals.models.Approval.objects", qs
            ), mock.patch(
                "Django_xm.apps.chat.models.ChatSession.objects"
            ) as chat_objs, mock.patch.object(
                mgr, "start_session", new_callable=mock.AsyncMock
            ) as start:
                # sess-live 存活于 DB（否则会被孤儿收编路径移除，绕过内存查重分支）
                chat_objs.filter.return_value.values_list.return_value = ["sess-live"]
                await mgr._recover_chat_approvals()
            start.assert_not_awaited()

        asyncio.run(_case())

    def test_start_session_failure_does_not_block_other_sessions(self):
        """单个会话重建失败不阻断其余会话恢复。"""
        async def _case():
            mgr = SessionManager(max_concurrency=5)
            rows = [
                SimpleNamespace(source_id="s-bad", user_id=1, extra={"graph_interrupt_id": "g1", "message_id": "1"}),
                SimpleNamespace(source_id="s-ok", user_id=1, extra={"graph_interrupt_id": "g2", "message_id": "2"}),
            ]
            qs = mock.Mock()
            qs.filter.return_value.order_by.return_value = rows

            async def _start(payload):
                if payload["thread_id"] == "s-bad":
                    raise RuntimeError("boom")

            with mock.patch(
                "Django_xm.apps.approvals.models.Approval.objects", qs
            ), mock.patch(
                "Django_xm.apps.chat.models.ChatSession.objects"
            ) as chat_objs, mock.patch.object(
                mgr, "start_session", side_effect=_start
            ):
                chat_objs.filter.return_value.values_list.return_value = ["s-bad", "s-ok"]
                await mgr._recover_chat_approvals()  # 不抛异常

        asyncio.run(_case())

    def test_orphan_session_approvals_abandoned_not_rebuilt(self):
        """B4 勘误：会话已删除/不存在的孤儿审批 → 扫描期收编（TIMEOUT），不重建执行器。"""
        async def _case():
            mgr = SessionManager(max_concurrency=5)
            rows = [
                SimpleNamespace(
                    source_id="sess-ghost",
                    user_id=1,
                    extra={"graph_interrupt_id": "g1", "message_id": "1"},
                ),
            ]
            qs = mock.Mock()
            qs.filter.return_value.order_by.return_value = rows
            with mock.patch(
                "Django_xm.apps.approvals.models.Approval.objects", qs
            ), mock.patch(
                "Django_xm.apps.chat.models.ChatSession.objects"
            ) as chat_objs, mock.patch(
                "Django_xm.apps.approvals.services.approval_batch.abandon_session_pending_approvals"
            ) as abandon, mock.patch.object(
                mgr, "start_session", new_callable=mock.AsyncMock
            ) as start:
                chat_objs.filter.return_value.values_list.return_value = []  # 会话不存在
                await mgr._recover_chat_approvals()
            start.assert_not_awaited()
            abandon.assert_called_once()
            from Django_xm.apps.approvals.models import Approval

            assert abandon.call_args.args[0] == Approval.Source.CHAT
            assert abandon.call_args.args[1] == "sess-ghost"

        asyncio.run(_case())


if __name__ == "__main__":
    unittest.main()
