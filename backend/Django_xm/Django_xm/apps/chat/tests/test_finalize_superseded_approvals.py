"""_finalize_superseded_approvals 显式 session_id 传参修复测试（阶段 7 票①）。

缺陷：方法体引用 ``self.thread_id``，而 ChatService 及三个 mixin 均未赋值该
属性（``__init__`` 的 thread_id 形参只透传给 ContextService）→ AttributeError
→ 调用点 except 吞为 warning → 自动拒绝后 CHAT 审批行永挂 PENDING →
``_recover_chat_approvals`` 反复重建执行器（B4 目标失效）。

定位键语义证据（Approval.source_id = chat session_id，非 graph thread_id）：
- 创建点 ``stream/loop.py`` ``request_approval_async(source_id=ctx.session_id)``,
  而 ``ctx.session_id = data.get("session_id", "")``（chat_stream_mixin._init_stream_context）；
- 消费端 ``session_manager._recover_chat_approvals._scan`` 以 ``row.source_id``
  作会话键并与 ``ChatSession.session_id`` 对账（孤儿判定）；
- ``approvals/models.py`` source_id 字段注释"session_id 或 task_id"；
- thread_id 与 session_id 的恒等（agent_service: ``thread_id = session_id or
  str(uuid.uuid4())``）仅在 session_id 非空时成立——空会话时 thread_id 兜底
  uuid4，与 source_id="" 不同源，故透传 data 的 session_id（与创建点同源）。

修复：显式传参——``_auto_reject_pending_interrupts(agent, thread_config,
session_id)`` 自 chat_service 调用点取 ``data["session_id"]`` 穿透至
``_finalize_superseded_approvals(resume_id, session_id)``。

ChatService 实例经 ``object.__new__`` 构造（绕过子服务重型初始化，沿用
test_subagent_content_snapshot._make_service 先例；本组用例仅覆盖审批定位）。

运行命令（backend/Django_xm 目录）：
D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe manage.py test \\
    Django_xm.apps.chat.tests.test_finalize_superseded_approvals \\
    --settings=Django_xm.settings.test --noinput
"""

import asyncio
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.chat.services.chat_service import ChatService

# _finalize_superseded_approvals 方法体内延迟导入 timeout_approval，patch 其源模块属性
TIMEOUT_TARGET = "Django_xm.apps.approvals.services.approval_service.timeout_approval"


def _make_service() -> ChatService:
    """构造绕过子服务初始化的 ChatService（仅覆盖审批定位，无需子服务）。"""
    return object.__new__(ChatService)


def _make_approval(interrupt_id: str, resume_id: str, session_id: str, state: str) -> Approval:
    """构造 chat 审批行（字段取值对齐 loop.py request_approval_async 创建点）。"""
    return Approval.objects.create(
        interrupt_id=interrupt_id,
        source=Approval.Source.CHAT,
        source_id=session_id,
        tool_name="shell_exec",
        state=state,
        action=Approval.ACTION_CONFIRM,
        parameters={},
        extra={"langgraph_resume_id": resume_id, "graph_interrupt_id": f"gi-{resume_id}"},
    )


class FinalizeSupersededApprovalsFilterTests(TestCase):
    """定位过滤与返回计数：按 (source, source_id, resume_id, PENDING) 四元组过滤。"""

    def setUp(self):
        self.svc = _make_service()
        # 命中：同会话同批次的两条 PENDING
        _make_approval("tc-1", "lr-1", "sess-x", Approval.State.PENDING)
        _make_approval("tc-2", "lr-1", "sess-x", Approval.State.PENDING)
        # 不命中：异批次 / 异会话 / 已终态
        _make_approval("tc-3", "lr-2", "sess-x", Approval.State.PENDING)
        _make_approval("tc-4", "lr-1", "sess-y", Approval.State.PENDING)
        _make_approval("tc-5", "lr-1", "sess-x", Approval.State.TIMEOUT)

    def test_finalize_filters_and_returns_count(self):
        """filter 按 session_id 定位执行，返回命中计数并逐条 TIMEOUT 终态化。"""
        with mock.patch(TIMEOUT_TARGET) as timeout_mock:
            count = self.svc._finalize_superseded_approvals("lr-1", "sess-x")

        self.assertEqual(count, 2)
        self.assertEqual(
            sorted(c.args[0] for c in timeout_mock.call_args_list),
            ["tc-1", "tc-2"],
        )
        for c in timeout_mock.call_args_list:
            self.assertEqual(c.kwargs, {"dispatch_resume": False})

    def test_finalize_no_match_returns_zero(self):
        """无命中批次返回 0 且不触发终态化。"""
        with mock.patch(TIMEOUT_TARGET) as timeout_mock:
            count = self.svc._finalize_superseded_approvals("lr-none", "sess-x")

        self.assertEqual(count, 0)
        timeout_mock.assert_not_called()


class AutoRejectSessionIdPassthroughTests(TestCase):
    """调用链透传：_auto_reject_pending_interrupts 将 session_id 传至终态化方法。"""

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_session_id_passed_to_finalize(self):
        svc = _make_service()
        intr = SimpleNamespace(id="intr-1")
        task = SimpleNamespace(interrupts=[intr])
        agent = SimpleNamespace(
            graph=SimpleNamespace(
                aget_state=mock.AsyncMock(return_value=SimpleNamespace(tasks=[task])),
                ainvoke=mock.AsyncMock(return_value=None),
            )
        )
        with mock.patch.object(ChatService, "_finalize_superseded_approvals") as fin_mock:
            self.run_async(
                svc._auto_reject_pending_interrupts(
                    agent, {"configurable": {"thread_id": "sess-x"}}, session_id="sess-x"
                )
            )

        fin_mock.assert_called_once_with("intr-1", "sess-x")
