"""审批状态机与查询 API 单元测试。

覆盖 Django_xm.apps.approvals.services.approval_service 的状态机与查询链路：
- resume_approval：pending 单条（无批次）直达 processing 的批准/拒绝/confirm_with_input
  路径、批次场景非最后确认者落 waiting、终态与 processing 的幂等返回、锁与广播副作用
- complete_approval：从 processing / pending 完成落库（resolved_at、临时字段清理、
  extra 合并）、批次锁释放、同批次终态化委托、记录不存在静默跳过、重复完成幂等
- timeout_approval：dispatch_resume=True / False 双分支（状态落库、恢复路由调用与否）、
  非 pending 幂等、锁占用跳过
- get_pending_approvals（source_id / chat_session_id 过滤）、
  get_approval_history_by_source（委托 Redis store 的透传契约）
- build_approval_extra：路由 ID setdefault、tool_config / model_config 默认值

mock 策略（与 test_approval_batch_resume.py 一致）：
- 模块级 patch _acquire_lock_with_retry / _acquire_lock / _release_lock /
  _persist_and_broadcast / _publish_tool_call_timeout_event，隔离 Redis 与广播副作用
- timeout 的恢复路由 gateway.route_timeout 与 complete 的批次终态化
  approval_lifecycle.service.complete_batch 均为函数内延迟导入，
  patch 其源模块单例属性即可拦截

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.approvals.tests.test_approval_state_machine --settings=Django_xm.settings.test

注：test settings 直接继承 base.py 的 PostgreSQL 配置（docker 容器 postgres_db），
Django 自动创建 test_ 前缀临时库；ai_engine 迁移中的 PG 专属 SQL 可正常执行。
"""

import os
from typing import Any
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service
from Django_xm.apps.approvals.services.approval_service import (
    build_approval_extra,
    complete_approval,
    get_approval_history_by_source,
    get_pending_approvals,
    resume_approval,
    timeout_approval,
)
from Django_xm.common import approval_gateway
from Django_xm.common.constants import TIMEOUT_DECISION


def _make_approval(
    interrupt_id: str,
    state: str = Approval.STATE_PENDING,
    graph_interrupt_id: str | None = None,
    action: str = Approval.ACTION_CONFIRM,
    source_id: str = "session-1",
    chat_session_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Approval:
    """构造一条审批记录（默认 pending / confirm / chat 来源）。"""
    merged_extra: dict[str, Any] = dict(extra or {})
    if graph_interrupt_id is not None:
        merged_extra.setdefault("graph_interrupt_id", graph_interrupt_id)
    return Approval.objects.create(
        interrupt_id=interrupt_id,
        source="chat",
        source_id=source_id,
        chat_session_id=chat_session_id if chat_session_id is not None else f"cs-{source_id}",
        tool_name="shell_exec",
        state=state,
        action=action,
        extra=merged_extra,
    )


class ResumeApprovalStateMachineTests(TestCase):
    """resume_approval 状态机：单条直达 / 拒绝 / 带输入确认 / 批次 waiting / 幂等。"""

    def setUp(self) -> None:
        patchers = [
            mock.patch.object(approval_service, "_acquire_lock_with_retry", return_value=True),
            mock.patch.object(approval_service, "_release_lock"),
            mock.patch.object(approval_service, "_persist_and_broadcast"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.acquire_retry_mock = approval_service._acquire_lock_with_retry
        self.release_lock_mock = approval_service._release_lock
        self.pab_mock = approval_service._persist_and_broadcast

    def test_resume_not_found_returns_idempotent_not_found(self) -> None:
        """审批不存在：返回 not_found=True 的幂等结构，不触碰锁与广播。"""
        result = resume_approval("missing_call", approved=True)

        self.assertIsNone(result["approval"])
        self.assertIsNone(result["resume_value"])
        self.assertIsNone(result["stream_generator"])
        self.assertTrue(result["idempotent"])
        self.assertTrue(result["not_found"])
        self.acquire_retry_mock.assert_not_called()
        self.pab_mock.assert_not_called()

    def test_resume_pending_single_approval_approved_goes_processing(self) -> None:
        """单条无批次：批准后直达 processing，落库 resume_value 并广播。"""
        approval = _make_approval("call_single_ok")

        result = resume_approval("call_single_ok", approved=True)

        # 返回结构：不含 "state" 键（调用方据此触发恢复流程）
        self.assertNotIn("state", result)
        self.assertNotIn("idempotent", result)
        self.assertEqual(result["approval"].interrupt_id, "call_single_ok")
        self.assertIs(result["resume_value"], True)
        self.assertIsNone(result["stream_generator"])

        # 落库：状态 processing，extra 记录恢复值与决策
        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_PROCESSING)
        self.assertIs(approval.extra["_resume_value"], True)
        self.assertIs(approval.extra["_approved"], True)

        # 副作用：广播 processing；无批次时锁 key 为 interrupt_id 本身
        self.pab_mock.assert_called_once_with(approval, Approval.STATE_PROCESSING)
        self.release_lock_mock.assert_called_once_with("call_single_ok")

    def test_resume_pending_single_approval_rejected_still_processing(self) -> None:
        """单条拒绝路径：resume_value=False，仍进入 processing（终态 rejected 由执行器 complete）。"""
        approval = _make_approval("call_single_reject")

        result = resume_approval("call_single_reject", approved=False)

        self.assertIs(result["resume_value"], False)
        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_PROCESSING)
        self.assertIs(approval.extra["_resume_value"], False)
        self.assertIs(approval.extra["_approved"], False)
        self.pab_mock.assert_called_once_with(approval, Approval.STATE_PROCESSING)

    def test_resume_confirm_with_input_uses_user_input_as_resume_value(self) -> None:
        """confirm_with_input 动作：resume_value 取 user_input（缺省为空串）并落库。"""
        approval = _make_approval("call_input", action=Approval.ACTION_CONFIRM_WITH_INPUT)

        result = resume_approval("call_input", approved=True, user_input="目标目录=/data")

        self.assertEqual(result["resume_value"], "目标目录=/data")
        approval.refresh_from_db()
        self.assertEqual(approval.user_input, "目标目录=/data")
        self.assertEqual(approval.extra["_resume_value"], "目标目录=/data")

        # 缺省 user_input → 空串
        approval2 = _make_approval("call_input_empty", action=Approval.ACTION_CONFIRM_WITH_INPUT)
        result2 = resume_approval("call_input_empty", approved=True)
        self.assertEqual(result2["resume_value"], "")

    def test_resume_batch_rejected_non_last_lands_waiting(self) -> None:
        """批次角度（既有 batch_resume 测试未覆盖）：先拒绝的确认者落 waiting，
        最后确认者（批准）触发恢复；批次锁 key 为 graph_interrupt_id。"""
        gid = "gid-reject-batch"
        _make_approval("call_r0", graph_interrupt_id=gid)
        _make_approval("call_r1", graph_interrupt_id=gid)

        result_reject = resume_approval("call_r0", approved=False)
        self.assertEqual(result_reject["state"], Approval.STATE_WAITING)
        self.assertIs(result_reject["resume_value"], False)
        self.assertEqual(
            Approval.objects.get(interrupt_id="call_r0").state, Approval.STATE_WAITING
        )

        result_last = resume_approval("call_r1", approved=True)
        self.assertNotIn("state", result_last)
        self.assertIs(result_last["resume_value"], True)
        self.assertEqual(
            Approval.objects.get(interrupt_id="call_r1").state, Approval.STATE_PROCESSING
        )
        # 先决断者保持 waiting，等待批次恢复后统一消费
        self.assertEqual(
            Approval.objects.get(interrupt_id="call_r0").state, Approval.STATE_WAITING
        )

        # 批次级锁：两次确认均以 graph_interrupt_id 为锁 key
        self.assertEqual(
            [c.args[0] for c in self.release_lock_mock.call_args_list], [gid, gid]
        )

    def test_resume_on_terminal_state_returns_idempotent(self) -> None:
        """终态后重复 resume：幂等返回，不改库、不加锁、不广播。"""
        for terminal in (Approval.STATE_APPROVED, Approval.STATE_REJECTED, Approval.STATE_TIMEOUT):
            with self.subTest(terminal=terminal):
                approval = _make_approval(f"call_term_{terminal}", state=terminal)

                result = resume_approval(f"call_term_{terminal}", approved=True)

                self.assertTrue(result["idempotent"])
                self.assertIsNone(result["resume_value"])
                self.assertNotIn("state", result)
                self.assertNotIn("not_found", result)
                self.assertEqual(result["approval"].pk, approval.pk)
                approval.refresh_from_db()
                self.assertEqual(approval.state, terminal)
                self.acquire_retry_mock.assert_not_called()
                self.pab_mock.assert_not_called()

    def test_resume_on_processing_returns_idempotent_with_state(self) -> None:
        """processing 中重复 resume：幂等返回并携带 state=processing，不进入锁竞争。"""
        approval = _make_approval("call_processing", state=Approval.STATE_PROCESSING)

        result = resume_approval("call_processing", approved=True)

        self.assertTrue(result["idempotent"])
        self.assertEqual(result["state"], Approval.STATE_PROCESSING)
        self.assertIsNone(result["resume_value"])
        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_PROCESSING)
        self.acquire_retry_mock.assert_not_called()
        self.pab_mock.assert_not_called()


class CompleteApprovalTests(TestCase):
    """complete_approval：终态落库、临时字段清理、批次委托与幂等。"""

    def setUp(self) -> None:
        patchers = [
            mock.patch.object(approval_service, "_release_lock"),
            mock.patch.object(approval_service, "_persist_and_broadcast"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.release_lock_mock = approval_service._release_lock
        self.pab_mock = approval_service._persist_and_broadcast

    def test_complete_from_processing_lands_terminal_and_cleans_temp_keys(self) -> None:
        """从 processing 完成：状态/resolved_at 落库，临时键清理，extra 合并，批次锁释放。"""
        approval = Approval.objects.create(
            interrupt_id="call_c1",
            source="chat",
            source_id="session-c",
            chat_session_id="cs-session-c",
            tool_name="shell_exec",
            state=Approval.STATE_PROCESSING,
            action=Approval.ACTION_CONFIRM,
            extra={
                "graph_interrupt_id": "gid-c",
                "_resume_value": True,
                "_approved": True,
                "_timeout": False,
                "message_id": "m-1",
            },
        )

        with mock.patch(
            "Django_xm.common.approval_lifecycle.service.complete_batch"
        ) as complete_batch_mock:
            complete_approval("call_c1", Approval.STATE_APPROVED, extra={"result": "ok"})

        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_APPROVED)
        self.assertIsNotNone(approval.resolved_at)
        # 临时键清理，业务字段保留，extra 参数合并
        extra = approval.extra
        for temp_key in ("_resume_value", "_approved", "_timeout"):
            self.assertNotIn(temp_key, extra)
        self.assertEqual(extra["graph_interrupt_id"], "gid-c")
        self.assertEqual(extra["message_id"], "m-1")
        self.assertEqual(extra["result"], "ok")

        self.pab_mock.assert_called_once_with(approval, Approval.STATE_APPROVED, {"result": "ok"})
        # 锁 key 与 resume 对称：批次维度优先
        self.release_lock_mock.assert_called_once_with("gid-c")
        # 同批次 waiting 兄弟终态化委托（graph_interrupt_id, interrupt_id, state）
        complete_batch_mock.assert_called_once_with("gid-c", "call_c1", Approval.STATE_APPROVED)

    def test_complete_from_pending_without_batch(self) -> None:
        """从 pending 直接完成（无批次）：同样落终态，锁 key 回退为 interrupt_id。"""
        approval = _make_approval("call_c2")

        with mock.patch(
            "Django_xm.common.approval_lifecycle.service.complete_batch"
        ) as complete_batch_mock:
            complete_approval("call_c2", Approval.STATE_REJECTED)

        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_REJECTED)
        self.assertIsNotNone(approval.resolved_at)
        self.pab_mock.assert_called_once_with(approval, Approval.STATE_REJECTED, None)
        self.release_lock_mock.assert_called_once_with("call_c2")
        complete_batch_mock.assert_called_once_with("", "call_c2", Approval.STATE_REJECTED)

    def test_complete_missing_record_is_silent_noop(self) -> None:
        """记录不存在：静默返回，无任何副作用。"""
        self.assertIsNone(complete_approval("ghost_call", Approval.STATE_APPROVED))
        self.pab_mock.assert_not_called()
        self.release_lock_mock.assert_not_called()

    def test_complete_twice_keeps_terminal_state(self) -> None:
        """重复完成：幂等，状态保持终态不回退。"""
        approval = _make_approval("call_c3", state=Approval.STATE_PROCESSING)

        with mock.patch("Django_xm.common.approval_lifecycle.service.complete_batch"):
            complete_approval("call_c3", Approval.STATE_APPROVED)
            complete_approval("call_c3", Approval.STATE_APPROVED)

        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_APPROVED)


class TimeoutApprovalTests(TestCase):
    """timeout_approval：dispatch_resume 双分支与幂等语义。

    注意：timeout_approval 使用 _acquire_lock（单次 SETNX）而非
    _acquire_lock_with_retry，mock 点与 resume 不同。
    """

    def setUp(self) -> None:
        patchers = [
            mock.patch.object(approval_service, "_acquire_lock", return_value=True),
            mock.patch.object(approval_service, "_release_lock"),
            mock.patch.object(approval_service, "_persist_and_broadcast"),
            mock.patch.object(approval_service, "_publish_tool_call_timeout_event"),
            mock.patch("Django_xm.common.approval_gateway.gateway.route_timeout"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.acquire_lock_mock = approval_service._acquire_lock
        self.pab_mock = approval_service._persist_and_broadcast
        self.publish_timeout_mock = approval_service._publish_tool_call_timeout_event
        self.route_timeout_mock = approval_gateway.gateway.route_timeout

    def test_timeout_dispatch_true_persists_and_routes_resume(self) -> None:
        """dispatch_resume=True：落库 TIMEOUT，两次广播，发布超时工具事件并路由恢复。"""
        approval = _make_approval("call_t1", graph_interrupt_id="gid-t")

        timeout_approval("call_t1", dispatch_resume=True)

        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_TIMEOUT)
        self.assertEqual(approval.extra["_resume_value"], TIMEOUT_DECISION)
        self.assertIs(approval.extra["_approved"], False)
        self.assertIs(approval.extra["_timeout"], True)

        # 中间态 PROCESSING（suppress_tool_event）→ 终态 TIMEOUT（extra={"timeout": True}）
        calls = self.pab_mock.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], mock.call(approval, Approval.STATE_PROCESSING, suppress_tool_event=True))
        self.assertEqual(calls[1], mock.call(approval, Approval.STATE_TIMEOUT, extra={"timeout": True}))

        self.publish_timeout_mock.assert_called_once_with(approval)
        self.route_timeout_mock.assert_called_once_with(approval, resume_value=TIMEOUT_DECISION)

    def test_timeout_dispatch_false_skips_resume_routing(self) -> None:
        """dispatch_resume=False：仅终态化，不触发恢复分发（供自愈/批次判定内部调用）。"""
        approval = _make_approval("call_t2", graph_interrupt_id="gid-t2")

        timeout_approval("call_t2", dispatch_resume=False)

        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_TIMEOUT)
        self.assertEqual(approval.extra["_resume_value"], TIMEOUT_DECISION)
        self.publish_timeout_mock.assert_called_once_with(approval)
        self.route_timeout_mock.assert_not_called()

    def test_timeout_non_pending_state_is_noop(self) -> None:
        """非 pending 状态：直接返回，不改库、不加锁、不广播、不路由。"""
        approval = _make_approval("call_t3", state=Approval.STATE_PROCESSING)

        timeout_approval("call_t3", dispatch_resume=True)

        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_PROCESSING)
        self.acquire_lock_mock.assert_not_called()
        self.pab_mock.assert_not_called()
        self.route_timeout_mock.assert_not_called()

    def test_timeout_missing_record_is_noop(self) -> None:
        """记录不存在：静默返回。"""
        timeout_approval("ghost_timeout", dispatch_resume=True)
        self.pab_mock.assert_not_called()
        self.route_timeout_mock.assert_not_called()

    def test_timeout_lock_unavailable_skips_processing(self) -> None:
        """锁被占用（并发）：跳过超时处理，状态保持 pending。"""
        approval = _make_approval("call_t4")

        with mock.patch.object(approval_service, "_acquire_lock", return_value=False):
            timeout_approval("call_t4", dispatch_resume=True)

        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_PENDING)
        self.pab_mock.assert_not_called()
        self.route_timeout_mock.assert_not_called()


class ApprovalQueryTests(TestCase):
    """查询 API：get_pending_approvals 过滤与 get_approval_history_by_source 委托。"""

    def test_get_pending_approvals_filters_by_source_id(self) -> None:
        """按 source_id 过滤：仅返回该 source 的 pending 记录。"""
        _make_approval("q_p1", source_id="s1")
        _make_approval("q_p2", source_id="s2")
        _make_approval("q_p3", state=Approval.STATE_APPROVED, source_id="s1")
        _make_approval("q_p4", state=Approval.STATE_PROCESSING, source_id="s1")

        result_all = get_pending_approvals()
        self.assertEqual({a.interrupt_id for a in result_all}, {"q_p1", "q_p2"})

        result_s1 = get_pending_approvals(source_id="s1")
        self.assertEqual([a.interrupt_id for a in result_s1], ["q_p1"])

        result_s9 = get_pending_approvals(source_id="s9-none")
        self.assertEqual(result_s9, [])

    def test_get_pending_approvals_filters_by_chat_session_id(self) -> None:
        """按 chat_session_id 过滤：命中该会话的 pending 记录。"""
        _make_approval("q_c1", source_id="s1", chat_session_id="cs-A")
        _make_approval("q_c2", source_id="s2", chat_session_id="cs-B")
        _make_approval("q_c3", state=Approval.STATE_TIMEOUT, source_id="s1", chat_session_id="cs-A")

        result = get_pending_approvals(chat_session_id="cs-A")
        self.assertEqual([a.interrupt_id for a in result], ["q_c1"])

        combined = get_pending_approvals(source_id="s1", chat_session_id="cs-B")
        self.assertEqual(combined, [])

    def test_get_approval_history_by_source_delegates_to_store(self) -> None:
        """get_approval_history_by_source：透传 source_id 并原样返回 store 结果。"""
        history = [
            {"interrupt_id": "h1", "state": Approval.STATE_APPROVED},
            {"interrupt_id": "h2", "state": Approval.STATE_PENDING},
        ]
        with mock.patch.object(
            approval_service, "_get_approval_history_from_store", return_value=history
        ) as store_mock:
            result = get_approval_history_by_source("session-h")

        store_mock.assert_called_once_with("session-h")
        self.assertEqual(result, history)


class BuildApprovalExtraTests(TestCase):
    """build_approval_extra：路由 ID 与配置默认值构造。"""

    def test_builds_routing_ids_and_config_defaults(self) -> None:
        """路由 ID 写入 extra；tool_config / model_config 取 data 值并带默认。"""
        data = {
            "use_tools": False,
            "provider_id": "prov-1",
            "model_name": "model-x",
            "tool_tier": "pro",
        }

        extra = build_approval_extra(
            data,
            tool_call_id="tc-1",
            graph_interrupt_id="gid-1",
            langgraph_resume_id="lr-1",
            message_id="msg-1",
        )

        self.assertEqual(extra["tool_call_id"], "tc-1")
        self.assertEqual(extra["graph_interrupt_id"], "gid-1")
        self.assertEqual(extra["langgraph_resume_id"], "lr-1")
        self.assertEqual(extra["message_id"], "msg-1")

        tool_config = extra["tool_config"]
        self.assertIs(tool_config["use_tools"], False)
        self.assertIs(tool_config["use_web_search"], False)
        self.assertIs(tool_config["use_mcp"], False)
        self.assertEqual(tool_config["tool_tier"], "pro")
        self.assertEqual(tool_config["selected_knowledge_bases"], [])

        model_config = extra["model_config"]
        self.assertEqual(model_config["provider_id"], "prov-1")
        self.assertEqual(model_config["model_name"], "model-x")
        self.assertIs(model_config["use_deep_thinking"], False)

    def test_base_extra_values_are_preserved_not_overwritten(self) -> None:
        """base_extra 已有的路由 ID / tool_config 不被覆盖（setdefault 语义）。"""
        base = {
            "message_id": "existing-msg",
            "risk_level": "high",
            "tool_config": {"custom": True},
        }

        extra = build_approval_extra(
            {"message_id": "new-msg"},
            tool_call_id="tc-2",
            message_id="new-msg",
            base_extra=base,
        )

        self.assertEqual(extra["message_id"], "existing-msg")
        self.assertEqual(extra["tool_config"], {"custom": True})
        self.assertEqual(extra["risk_level"], "high")
        self.assertEqual(extra["tool_call_id"], "tc-2")
        # 浅拷贝：修改返回值不影响调用方传入的 base_extra
        self.assertIsNot(extra, base)

    def test_empty_inputs_produce_only_config_defaults(self) -> None:
        """全部可选参数为空：extra 仅含 tool_config / model_config 默认值。"""
        extra = build_approval_extra({})

        self.assertEqual(set(extra.keys()), {"tool_config", "model_config"})
        self.assertIs(extra["tool_config"]["use_tools"], True)
        self.assertEqual(extra["tool_config"]["tool_tier"], "standard")
        self.assertIsNone(extra["model_config"]["provider_id"])
        self.assertIsNone(extra["model_config"]["temperature"])
