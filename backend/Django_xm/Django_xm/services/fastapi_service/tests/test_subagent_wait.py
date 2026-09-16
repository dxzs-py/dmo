"""SubAgentRuntime 业务等待挂起（B2）单元测试。

覆盖：业务等待 interrupt 判定、wait_for_subagent 结果格式化、
SessionExecutor 挂起注册 awaiter、调度器唤醒（mismatch 忽略 / 正常恢复清理）、
run() finally 挂起态跳过清理（固化规则：保留会话槽 + 不释放 checkpointer）、
子代理审批归属（根会话标识判定 source/source_id）、configurable 契约
（tool_names 逐层继承）、FastAPI 重启恢复（configurable 内存 registry 丢失
→ metadata 兜底：路由键 / 三层兜底链 / routing_config 消费）。

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

from Django_xm.apps.ai_engine.models import SubAgentStatus
from Django_xm.apps.ai_engine.subagent_runtime.adapters.langgraph_adapter import LangGraphAdapter
from Django_xm.apps.tools.base import is_approval_interrupt, is_subagent_wait_interrupt
from Django_xm.services.fastapi_service.session_executor import SessionExecutor

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
            {"subagent_results": [{"subagent_thread_id": "sub1", "status": "completed", "result": "hello"}]}
        )
        self.assertIn("已完成", out)
        self.assertIn("hello", out)

    def test_failed(self):
        from Django_xm.apps.agent_hub.subagent_tools.wait import WaitForSubAgentTool

        out = WaitForSubAgentTool._format_resume_value(
            {"subagent_results": [{"subagent_thread_id": "sub1", "status": "failed", "result": ""}]}
        )
        self.assertIn("失败", out)


class TestHandleSuspend(unittest.TestCase):
    """_handle_suspend：置挂起态 + 注册父 awaiter（子代理未终态时）。"""

    def test_registers_awaiter(self):
        async def _case():
            ex = _make_executor()
            result = SimpleNamespace(interrupt_id="int1", subagent_thread_ids=["sub1"])
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
            self.assertEqual(ex._wait_subagent_thread_ids, ["sub1"])
            lm.register_parent_awaiter.assert_called_once_with("t1", make_awaiter.return_value)

        asyncio.run(_case())

    def test_second_suspend_clears_stale_results(self):
        """跨多轮 wait 竞态修复：新一轮挂起清空上一轮残留的子代理终态结果，
        防止 _register_waiter 兜底被旧数据误判"全部已终态"提前恢复父 Graph
        （根因：残留累积 → len(残留) >= len(新等待列表) 提前满足 → 任务提前
        completed 而子代理仍在后台运行）。"""
        async def _case():
            ex = _make_executor()
            # 模拟第一轮 wait 的残留（真实场景：跨多轮 wait_for_subagent 累积不清空）
            ex._pending_subagent_results = {"sub1": {"status": "completed", "result": "old"}}
            result = SimpleNamespace(interrupt_id="int2", subagent_thread_ids=["sub2"])
            runtime = mock.Mock()
            # sub2 仍运行中：若残留未清空，len({sub1}) >= len(["sub2"]) 会误判"全部已终态"
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

            # 修复生效断言：残留已清空、未提前恢复、正确注册 awaiter
            self.assertEqual(ex._pending_subagent_results, {})
            self.assertEqual(ex._wait_subagent_thread_ids, ["sub2"])
            lm.register_parent_awaiter.assert_called_once_with("t1", make_awaiter.return_value)

        asyncio.run(_case())

    def test_chat_wait_suspend_clears_stale_results(self):
        """chat 模式业务等待挂起同样清空残留（与 research 版 _handle_suspend 同构）。"""
        async def _case():
            ex = _make_executor()
            ex._pending_subagent_results = {"sub1": {"status": "completed", "result": "old"}}
            runtime = mock.Mock()
            runtime.get_instance = mock.AsyncMock(return_value=SimpleNamespace(status="running"))
            lm = mock.Mock()
            with mock.patch(
                "Django_xm.apps.ai_engine.subagent_runtime.get_subagent_runtime", return_value=runtime
            ), mock.patch(
                "Django_xm.apps.ai_engine.subagent_runtime.lifecycle.get_lifecycle_manager", return_value=lm
            ), mock.patch.object(
                SessionExecutor, "_make_parent_awaiter", return_value=mock.Mock()
            ) as make_awaiter:
                await ex._handle_chat_wait_suspend(
                    {"interrupt_id": "int2", "subagent_thread_ids": ["sub2"]}
                )

            self.assertEqual(ex._pending_subagent_results, {})
            self.assertEqual(ex._wait_subagent_thread_ids, ["sub2"])
            lm.register_parent_awaiter.assert_called_once_with("t1", make_awaiter.return_value)

        asyncio.run(_case())


class TestResumeAfterSubAgentWait(unittest.TestCase):
    """调度器唤醒：mismatch 忽略 / 正常恢复并清理。"""

    def test_ignores_non_waiting_subagent(self):
        async def _case():
            ex = _make_executor()
            ex._wait_subagent_thread_ids = ["sub1"]
            ex._suspended = True
            with mock.patch.object(
                SessionExecutor, "_execute_and_handle", new_callable=mock.AsyncMock
            ) as execute, mock.patch.object(
                SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock
            ) as release:
                await ex._on_subagent_finished("sub2", "completed")

            execute.assert_not_awaited()
            release.assert_not_awaited()
            ex.manager.remove_session.assert_not_called()

        asyncio.run(_case())

    def test_resumes_and_cleans_up(self):
        async def _case():
            ex = _make_executor()
            ex._agent = mock.Mock()
            ex._wait_subagent_thread_ids = ["sub1"]
            ex._wait_interrupt_id = "int1"
            ex._suspended = True
            ex._pending_subagent_results = {"sub1": {"status": "completed", "result": "result text"}}
            lm = mock.Mock()
            with mock.patch.object(
                SessionExecutor, "_execute_and_handle", new_callable=mock.AsyncMock
            ) as execute, mock.patch.object(
                SessionExecutor, "_release_checkpointer", new_callable=mock.AsyncMock
            ), mock.patch(
                "Django_xm.apps.ai_engine.subagent_runtime.lifecycle.get_lifecycle_manager", return_value=lm
            ):
                await ex._resume_after_subagent_wait()

            execute.assert_awaited_once()
            # Command(resume={interrupt_id: {"subagent_results": [...]}}) 的 interrupt_id 键必须匹配
            args, _ = execute.call_args
            resume_cmd = args[0]
            self.assertEqual(list(resume_cmd.resume.keys()), ["int1"])
            results = resume_cmd.resume["int1"]["subagent_results"]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "completed")
            self.assertEqual(results[0]["result"], "result text")
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


class TestSubAgentNestedWaitAdapter(unittest.TestCase):
    """子代理嵌套业务等待（langgraph_adapter 业务 wait 分支）。

    覆盖：wait 中断检测（与审批中断隔离）、竞态兜底（孙代理已全终态直接恢复）、
    awaiter 注册 + 孙代理终态累积恢复（runtime.resume 携带 subagent_results）。
    """

    @staticmethod
    def _make_adapter(runtime):
        return LangGraphAdapter(runtime, graph_factory=mock.AsyncMock())

    def test_detect_subagent_wait_interrupt(self):
        adapter = self._make_adapter(mock.Mock())
        state = SimpleNamespace(
            tasks=[
                SimpleNamespace(
                    interrupts=[
                        SimpleNamespace(
                            value={"_subagent_wait": True, "subagent_thread_ids": ["g1", "g2"]},
                            id="wait-int-1",
                        )
                    ]
                )
            ]
        )
        info = adapter._detect_subagent_wait_interrupt(state)
        self.assertEqual(info["subagent_thread_ids"], ["g1", "g2"])
        self.assertEqual(info["interrupt_id"], "wait-int-1")

    def test_detect_returns_none_for_approval_interrupt(self):
        adapter = self._make_adapter(mock.Mock())
        state = SimpleNamespace(
            tasks=[
                SimpleNamespace(
                    interrupts=[
                        SimpleNamespace(value={"_approval": True, "requests": []}, id="appr-1")
                    ]
                )
            ]
        )
        self.assertIsNone(adapter._detect_subagent_wait_interrupt(state))

    def test_wait_suspend_race_all_terminal_resumes_directly(self):
        """竞态兜底：孙代理在注册 awaiter 前已全部终态 → 直接恢复。"""
        runtime = mock.Mock()
        runtime._update_status = mock.AsyncMock()
        runtime.get_instance = mock.AsyncMock(
            return_value=SimpleNamespace(status=SubAgentStatus.COMPLETED, result_preview="r1")
        )
        runtime.resume = mock.AsyncMock()
        adapter = self._make_adapter(runtime)
        instance = SimpleNamespace(thread_id="sub_a", parent_thread_id="s1", metadata={})
        with mock.patch(
            "Django_xm.apps.ai_engine.subagent_runtime.lifecycle.get_lifecycle_manager"
        ) as glm:
            lm = mock.Mock()
            glm.return_value = lm
            asyncio.run(
                adapter._handle_subagent_wait_suspend(
                    instance, {"subagent_thread_ids": ["g1"], "interrupt_id": "wait-1"}
                )
            )
        runtime._update_status.assert_awaited_with(
            "sub_a",
            SubAgentStatus.RUNNING,
            pending_interrupt_info={"interrupt_id": "wait-1", "_subagent_wait": True},
        )
        runtime.resume.assert_awaited_once()
        payload = runtime.resume.await_args.args[1]
        self.assertEqual(
            payload,
            {"subagent_results": [{"subagent_thread_id": "g1", "status": "completed", "result": "r1"}]},
        )
        lm.register_parent_awaiter.assert_not_called()

    def test_wait_suspend_registers_awaiter_and_resumes(self):
        """孙代理未终态 → 注册 awaiter；终态后累积结果并恢复。"""
        runtime = mock.Mock()
        runtime._update_status = mock.AsyncMock()
        runtime.get_instance = mock.AsyncMock(
            return_value=SimpleNamespace(status=SubAgentStatus.RUNNING, result_preview="")
        )
        adapter = self._make_adapter(runtime)
        instance = SimpleNamespace(thread_id="sub_a", parent_thread_id="s1", metadata={})
        with mock.patch(
            "Django_xm.apps.ai_engine.subagent_runtime.lifecycle.get_lifecycle_manager"
        ) as glm:
            lm = mock.Mock()
            glm.return_value = lm
            asyncio.run(
                adapter._handle_subagent_wait_suspend(
                    instance, {"subagent_thread_ids": ["g1"], "interrupt_id": "wait-1"}
                )
            )
            self.assertEqual(lm.register_parent_awaiter.call_count, 1)
            awaiter = lm.register_parent_awaiter.call_args.args[1]

            # 孙代理终态：累积结果并触发恢复（awaiter 内 unregister 落在同一 mock manager）
            runtime.get_instance = mock.AsyncMock(
                return_value=SimpleNamespace(status=SubAgentStatus.COMPLETED, result_preview="r2")
            )
            runtime.resume = mock.AsyncMock()
            asyncio.run(awaiter("g1", SubAgentStatus.COMPLETED))
        runtime.resume.assert_awaited_once_with(
            "sub_a",
            {"subagent_results": [{"subagent_thread_id": "g1", "status": "completed", "result": "r2"}]},
        )
        lm.unregister_parent_awaiter.assert_called_once_with("sub_a")


class TestSubAgentConfigurableContract(unittest.TestCase):
    """子代理 configurable 契约（嵌套继承闭环，替代全局旁路）。

    ``_build_configurable`` 写入 tool_names（子代理自身 AgentConfig.tools）与
    运行配置（user_id/session_id/model_name/store/enable_deep_thinking）。
    孙代理 spawn 时从父（=本子代理）configurable 读取完整工具集，
    任意深度嵌套逐层覆盖传递，不依赖任何外部注册/清除时序。
    """

    def _make_adapter(self):
        return LangGraphAdapter(mock.Mock(), graph_factory=mock.AsyncMock())

    def test_carries_tools_and_config(self):
        t1 = SimpleNamespace(name="shell_exec")
        t2 = SimpleNamespace(name="spawn_sub_agent")
        agent_config = SimpleNamespace(
            tools=[t1, t2],
            user_id=1,
            session_id="s1",
            model_name="deepseek",
            store=None,
            enable_deep_thinking=True,
        )
        instance = SimpleNamespace(
            thread_id="sub_a",
            metadata={"depth": 1, "agent_name": "web-researcher", "risk_ceiling": None},
        )
        cfg = self._make_adapter()._build_configurable(
            instance, {"chat_session_id": "s1"}, agent_config
        )
        self.assertEqual(cfg["thread_id"], "sub_a")
        self.assertEqual(cfg["tool_names"], ["shell_exec", "spawn_sub_agent"])
        self.assertEqual(cfg["user_id"], 1)
        self.assertEqual(cfg["session_id"], "s1")
        self.assertEqual(cfg["model_name"], "deepseek")
        self.assertTrue(cfg["enable_deep_thinking"])
        self.assertEqual(cfg["agent_path"], ["main", "web-researcher"])
        self.assertEqual(cfg["chat_session_id"], "s1")

    def test_without_agent_config_skips_tool_contract(self):
        instance = SimpleNamespace(
            thread_id="sub_a",
            metadata={"depth": 1, "agent_name": "general-purpose", "risk_ceiling": None},
        )
        callback = mock.AsyncMock()
        cfg = self._make_adapter()._build_configurable(
            instance,
            {"_on_tool_event": callback, "assistant_message_id": "m1"},
            None,
        )
        self.assertIs(cfg["_on_tool_event"], callback)
        self.assertNotIn("tool_names", cfg)

    def test_metadata_fallback_when_configurable_lost(self):
        """FastAPI 重启恢复：内存 configurable registry 丢失（configurable=None）
        → 路由键 chat_session_id / assistant_message_id 从持久化 metadata 兜底，
        保证 graph 内消费者与孙代理 spawn 继承不断链。"""
        instance = SimpleNamespace(
            thread_id="sub_a",
            metadata={
                "depth": 1,
                "agent_name": "web-researcher",
                "risk_ceiling": None,
                "chat_session_id": "main-session",
                "assistant_message_id": "msg-42",
            },
        )
        cfg = self._make_adapter()._build_configurable(instance, None, None)
        self.assertEqual(cfg["chat_session_id"], "main-session")
        self.assertEqual(cfg["assistant_message_id"], "msg-42")

    def test_configurable_takes_priority_over_metadata(self):
        """正常运行：configurable 携带真实父链路值时优先，metadata 兜底不覆盖。"""
        instance = SimpleNamespace(
            thread_id="sub_a",
            metadata={
                "depth": 1,
                "agent_name": "web-researcher",
                "risk_ceiling": None,
                "chat_session_id": "stale-session",
                "assistant_message_id": "stale-msg",
            },
        )
        cfg = self._make_adapter()._build_configurable(
            instance,
            {"chat_session_id": "live-session", "assistant_message_id": "live-msg"},
            None,
        )
        self.assertEqual(cfg["chat_session_id"], "live-session")
        self.assertEqual(cfg["assistant_message_id"], "live-msg")


class TestSubAgentToolEventInterruptPassthrough(unittest.TestCase):
    """awrap_tool_call 对 LangGraph Interrupt 精确放行（不转发 FAILED）。

    业务等待挂起（wait_for_subagent 调 interrupt()）是框架级中断而非工具失败，
    必须以类型精确捕获放行，避免前端显示"失败"及恢复时 failed→completed
    非法转换。
    """

    def test_interrupt_passthrough_no_failed_event(self):
        from langgraph.errors import GraphInterrupt

        from Django_xm.apps.agent_hub.builders.subagent_support import SubAgentToolEventMiddleware

        mw = SubAgentToolEventMiddleware()
        mw._forward_event = mock.AsyncMock()
        request = mock.Mock()
        request.tool_call = {"name": "wait_for_subagent", "id": "tc1", "args": {}}
        request.state = mock.Mock()

        async def _execute(_req):
            raise GraphInterrupt({"_subagent_wait": True, "subagent_thread_ids": ["g1"]})

        with mock.patch(
            "Django_xm.apps.agent_hub.builders.subagent_support._read_subagent_thread_id",
            return_value="sub_a",
        ), mock.patch(
            "Django_xm.apps.agent_hub.builders.subagent_support._read_agent_name",
            return_value="web-researcher",
        ), mock.patch(
            "Django_xm.apps.agent_hub.builders.subagent_support._read_configurable",
            return_value={},
        ), self.assertRaises(GraphInterrupt):
            asyncio.run(mw.awrap_tool_call(request, _execute))
        mw._forward_event.assert_not_awaited()

    def test_real_exception_still_fails(self):
        from Django_xm.apps.agent_hub.builders.subagent_support import SubAgentToolEventMiddleware

        mw = SubAgentToolEventMiddleware()
        mw._forward_event = mock.AsyncMock()
        request = mock.Mock()
        request.tool_call = {"name": "shell_exec", "id": "tc2", "args": {}}
        request.state = mock.Mock()

        async def _execute(_req):
            raise RuntimeError("boom")

        with mock.patch(
            "Django_xm.apps.agent_hub.builders.subagent_support._read_subagent_thread_id",
            return_value="sub_a",
        ), mock.patch(
            "Django_xm.apps.agent_hub.builders.subagent_support._read_agent_name",
            return_value="web-researcher",
        ), mock.patch(
            "Django_xm.apps.agent_hub.builders.subagent_support._read_configurable",
            return_value={"_on_tool_event": mock.Mock()},
        ), self.assertRaises(RuntimeError):
            asyncio.run(mw.awrap_tool_call(request, _execute))
        mw._forward_event.assert_awaited_once()


class TestApprovalChannelRouting(unittest.TestCase):
    """chat 子代理审批频道修正（嵌套审批按钮不渲染的根因）。

    子代理审批的 source_id 是 subagent_xxx（子代理归属），_resolve_channels
    会把事件发到 session:subagent_xxx 频道，前端仅订阅主会话频道而收不到。
    修复：publish_approval 对 CHAT 模块用 approval 注入的 chat_session_id
    覆盖 session 频道，确保嵌套子代理（L2）的审批按钮正常渲染。
    """

    def _call_publish_approval(self, module_id: str, extra: dict | None):
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.realtime_sync import publish_approval

        async def run():
            with mock.patch("Django_xm.common.realtime_sync.publish_event") as pub:
                await publish_approval(
                    EventType.APPROVAL_PENDING,
                    interrupt_id="call_xxx",
                    tool_call_id="call_xxx",
                    module=EventSource.CHAT,
                    module_id=module_id,
                    state="pending",
                    tool_name="shell_exec",
                    message_id="1",
                    extra_fields=extra or {},
                )
                return pub.call_args.kwargs["session_id"]

        return asyncio.run(run())

    def test_subagent_approval_routed_to_main_session_channel(self):
        # B 的 shell 审批：source_id=subagent_A，须发到主会话频道
        session_id = self._call_publish_approval(
            "subagent_110b8c7c0a674400",
            {"chat_session_id": "6088ecf8-c51f-4892-951d-67cf8f974495"},
        )
        self.assertEqual(session_id, "6088ecf8-c51f-4892-951d-67cf8f974495")

    def test_main_session_approval_unchanged(self):
        # 主会话审批：source_id=主会话，频道保持主会话
        session_id = self._call_publish_approval(
            "6088ecf8-c51f-4892-951d-67cf8f974495",
            {"chat_session_id": "6088ecf8-c51f-4892-951d-67cf8f974495"},
        )
        self.assertEqual(session_id, "6088ecf8-c51f-4892-951d-67cf8f974495")

    def test_chat_without_chat_session_id_keeps_module_id(self):
        # 兼容：无 chat_session_id（历史/异常数据）时回退 module_id
        session_id = self._call_publish_approval("6088ecf8-c51f-4892-951d-67cf8f974495", {})
        self.assertEqual(session_id, "6088ecf8-c51f-4892-951d-67cf8f974495")


class TestSubagentApprovalAttribution(unittest.TestCase):
    """子代理审批归属判定（根会话标识，source/source_id 一致归集）。

    嵌套子代理（depth≥2）直接父是 subagent_xxx，按父推断会把深研嵌套审批
    误判为 chat（独立深研无 chat_session_id 时事件发到 subagent 频道，
    前端收不到审批按钮）。修复：用 configurable.session_id（各模块源头写入
    的根标识，chat=会话 id，深研=research task id）判定 source 与 source_id。
    """

    def _make_interrupt_state(self):
        return SimpleNamespace(
            tasks=[
                SimpleNamespace(
                    interrupts=[
                        SimpleNamespace(
                            value={
                                "_approval": True,
                                "requests": [{"tool_call_id": "tc1", "tool_name": "shell_exec"}],
                            },
                            id="g1",
                        )
                    ]
                )
            ]
        )

    def _call_create(self, configurable: dict | None, parent_thread_id: str, metadata: dict | None = None):
        from Django_xm.apps.ai_engine.subagent_runtime.adapters.langgraph_adapter import (
            LangGraphAdapter,
        )

        instance = SimpleNamespace(
            thread_id="subagent_b",
            parent_thread_id=parent_thread_id,
            metadata=metadata or {"depth": 2, "user_id": 1},
        )
        adapter = LangGraphAdapter(mock.Mock(), graph_factory=mock.AsyncMock())
        with mock.patch(
            "Django_xm.common.approval_parser.parse_approval_interrupt",
            return_value=[{"tool_call_id": "tc1", "tool_name": "shell_exec"}],
        ), mock.patch(
            "Django_xm.apps.approvals.services.approval_batch.create_approvals_for_interrupts"
        ) as create_approvals:
            asyncio.run(adapter._create_approvals_from_interrupts(instance, configurable, self._make_interrupt_state()))
            return create_approvals.call_args.kwargs

    def test_chat_nested_uses_root_session(self):
        # 代理模式嵌套：根标识=主会话 → source=chat, source_id=主会话
        kwargs = self._call_create(
            {"session_id": "main-session", "chat_session_id": "main-session"},
            "subagent_110b8c7c0a674400",
        )
        self.assertEqual(kwargs["source"], "chat")
        self.assertEqual(kwargs["source_id"], "main-session")
        self.assertEqual(kwargs["chat_session_id"], "main-session")

    def test_research_nested_uses_root_task(self):
        # 深研嵌套：根标识=research task id → source=deep_research, source_id=research task id
        kwargs = self._call_create(
            {"session_id": "research_task_1", "chat_session_id": "main-session"},
            "subagent_110b8c7c0a674400",
        )
        self.assertEqual(kwargs["source"], "deep_research")
        self.assertEqual(kwargs["source_id"], "research_task_1")

    def test_research_independent_no_chat_session(self):
        # 独立深研嵌套（无 chat_session_id）：source=deep_research，事件走 task 频道
        kwargs = self._call_create({"session_id": "research_task_1"}, "subagent_110b8c7c0a674400")
        self.assertEqual(kwargs["source"], "deep_research")
        self.assertEqual(kwargs["source_id"], "research_task_1")

    def test_fallback_to_parent_thread_id(self):
        # 兼容：无 configurable.session_id 时回退直接父线程
        kwargs = self._call_create(None, "subagent_110b8c7c0a674400")
        self.assertEqual(kwargs["source"], "chat")
        self.assertEqual(kwargs["source_id"], "subagent_110b8c7c0a674400")

    def test_metadata_fallback_when_configurable_none(self):
        """FastAPI 重启后内存 configurable registry 丢失（configurable=None）：
        审批创建从持久化 metadata 兜底 chat_session_id / assistant_message_id /
        session_id，不再触发『chat 模块审批缺少 chat_session_id，拒绝创建』。"""
        metadata = {
            "depth": 2,
            "user_id": 1,
            "session_id": "main-session",
            "chat_session_id": "main-session",
            "assistant_message_id": "msg-42",
        }
        kwargs = self._call_create(None, "subagent_110b8c7c0a674400", metadata=metadata)
        self.assertEqual(kwargs["source"], "chat")
        self.assertEqual(kwargs["source_id"], "main-session")
        self.assertEqual(kwargs["chat_session_id"], "main-session")
        self.assertEqual(kwargs["message_id"], "msg-42")


class TestSubAgentExecuteRoutingConfig(unittest.TestCase):
    """_execute 重启恢复：审批创建与状态事件消费 routing_config（经
    _build_configurable metadata 兜底后齐全），替代可能为 None 的 raw
    configurable（FastAPI 重启后 _configurable_registry 为空的场景）。"""

    def test_execute_uses_routing_config_from_metadata_fallback(self):
        runtime = mock.Mock()
        runtime._update_status = mock.AsyncMock()
        adapter = LangGraphAdapter(runtime, graph_factory=mock.AsyncMock())

        instance = SimpleNamespace(
            thread_id="sub_a",
            parent_thread_id="main-session",
            metadata={
                "depth": 1,
                "agent_name": "web-researcher",
                "task": "do research",
                "chat_session_id": "main-session",
                "assistant_message_id": "msg-42",
            },
        )

        # graph 工厂返回 mock agent：astream 空转，aget_state 返回审批中断 state
        agent = mock.Mock()

        async def _astream(*_args, **_kwargs):
            return
            yield  # pragma: no cover

        async def _aget_state(*_args, **_kwargs):
            return SimpleNamespace(
                next=("agent",),
                tasks=[
                    SimpleNamespace(
                        interrupts=[
                            SimpleNamespace(
                                value={
                                    "_approval": True,
                                    "requests": [{"tool_call_id": "tc1", "tool_name": "shell_exec"}],
                                },
                                id="int-1",
                            )
                        ]
                    )
                ],
            )

        agent.graph.astream = _astream
        agent.graph.aget_state = _aget_state
        adapter._graph_factory = mock.AsyncMock(return_value=agent)

        with mock.patch.object(
            adapter, "_publish_status_event", new_callable=mock.AsyncMock
        ) as publish, mock.patch.object(
            adapter, "_create_approvals_from_interrupts", new_callable=mock.AsyncMock
        ) as create:
            # 重启恢复场景：configurable=None（内存 registry 已丢失）
            asyncio.run(adapter._execute(instance, None, None, None))

        # 审批创建收到 routing_config：chat_session_id 经 metadata 兜底后齐全
        routing_cfg = create.call_args.args[1]
        self.assertEqual(routing_cfg["chat_session_id"], "main-session")
        self.assertEqual(routing_cfg["assistant_message_id"], "msg-42")
        # 状态事件同样消费 routing_config（而非 None）
        publish_cfg = publish.call_args.args[1]
        self.assertEqual(publish_cfg["chat_session_id"], "main-session")


if __name__ == "__main__":
    unittest.main()
