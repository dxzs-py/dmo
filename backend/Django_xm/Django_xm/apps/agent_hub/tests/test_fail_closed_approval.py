"""审批 fail-closed 语义单元测试（安全修复 fix-review-round2 Task 2）。

覆盖：
1. 2.1 未注册工具默认 CONTROLLED（UnregisteredToolApprovalPolicy 兜底，
   子 agent 中按写操作加权 CONTROLLED → HIGH，受 risk_ceiling 封顶）；
2. 2.1 只读工具白名单注册（ReadOnlyApprovalPolicy，SAFE 自动通过）与
   项目自定义 todo_write 注册（CONTROLLED）；
3. 2.1 aafter_model 端到端：未注册动态工具（如 MCP 前缀名）进入审批批次；
   wait_for_subagent 系统编排工具保持免审批（ctx + PENDING）；
4. 2.2 同步 after_model fail-closed：携带 tool_calls 时抛
   SyncApprovalNotSupportedError，纯文本轮放行；
5. 2.5 幂等集合构建失败 fail-closed：SAFE 级工具升级 CONTROLLED 送审。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.agent_hub.tests.test_fail_closed_approval --noinput
"""

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.async_utils import run_async
from Django_xm.apps.agent_hub.approval.middleware import (
    SYSTEM_ORCHESTRATION_TOOLS,
    ApprovalMiddleware,
    SyncApprovalNotSupportedError,
)
from Django_xm.apps.agent_hub.approval.policies import (
    READ_ONLY_TOOL_WHITELIST,
    ReadOnlyApprovalPolicy,
    TodoWriteFileApprovalPolicy,
    UnregisteredToolApprovalPolicy,
)
from Django_xm.common.risk_levels import RiskLevel
from langchain_core.messages import AIMessage


def _state_with_tool_calls(*tool_calls: dict) -> dict:
    return {"messages": [AIMessage(content="", tool_calls=list(tool_calls))]}


class TestUnregisteredToolPolicy(unittest.TestCase):
    """2.1：未注册工具兜底策略默认 CONTROLLED。"""

    def setUp(self):
        self.policy = UnregisteredToolApprovalPolicy(tool_name="mcp__dynamic_tool")

    def test_default_controlled_requires_approval(self):
        self.assertEqual(self.policy.assess_risk({"query": "x"}), RiskLevel.CONTROLLED)
        self.assertTrue(self.policy.should_approve({"query": "x"}))

    def test_subagent_weighting_escalates_to_high(self):
        """未知工具按可能有副作用处理：子 agent 中 CONTROLLED → HIGH。"""
        result = self.policy.assess_risk(
            {"query": "x"}, subagent_context={"risk_ceiling": None, "depth": 1}
        )
        self.assertEqual(result, RiskLevel.HIGH)

    def test_subagent_risk_ceiling_caps_at_controlled(self):
        result = self.policy.assess_risk(
            {"query": "x"}, subagent_context={"risk_ceiling": RiskLevel.CONTROLLED, "depth": 1}
        )
        self.assertEqual(result, RiskLevel.CONTROLLED)

    def test_danger_level_medium(self):
        self.assertEqual(self.policy.assess_danger({}), "medium")


class TestDefaultPolicyRegistry(unittest.TestCase):
    """2.1：默认注册表覆盖白名单与 todo_write。"""

    def setUp(self):
        self.registry = {p.tool_name: p for p in ApprovalMiddleware._default_policies()}

    def test_read_only_whitelist_registered_safe(self):
        for name in READ_ONLY_TOOL_WHITELIST:
            policy = self.registry.get(name)
            self.assertIsInstance(policy, ReadOnlyApprovalPolicy, f"{name} 未注册只读白名单策略")
            self.assertEqual(policy.assess_risk({}), RiskLevel.SAFE, f"{name} 应为 SAFE")
            self.assertFalse(policy.should_approve({}), f"{name} 不应要求审批")

    def test_todo_write_registered_controlled(self):
        policy = self.registry.get("todo_write")
        self.assertIsInstance(policy, TodoWriteFileApprovalPolicy)
        self.assertEqual(policy.assess_risk({}), RiskLevel.CONTROLLED)

    def test_whitelist_names_unique(self):
        """白名单与既有具名策略不得冲突（后者会被字典覆盖）。"""
        named = {p.tool_name for p in ApprovalMiddleware._default_policies()}
        self.assertEqual(len(named), len(ApprovalMiddleware._default_policies()))


class TestAafterModelFailClosed(SimpleTestCase):
    """2.1 端到端：未注册动态工具进入审批批次；编排工具豁免。"""

    def _middleware(self) -> ApprovalMiddleware:
        return ApprovalMiddleware()

    def test_unregistered_tool_enters_approval(self):
        mw = self._middleware()
        tool_call = {
            "name": "mcp__dynamic_tool",
            "args": {"query": "hello"},
            "id": "tc-mcp-1",
            "type": "tool_call",
        }
        with (
            patch.object(
                mw, "_build_idempotent_tc_ids", new=AsyncMock(return_value=(set(), {}))
            ),
            patch.object(mw, "_audit_auto_approved_tools", new=AsyncMock()) as audit_mock,
            patch(
                "Django_xm.apps.agent_hub.approval.middleware.interrupt",
                return_value=False,
            ) as interrupt_mock,
        ):
            run_async(mw.aafter_model(_state_with_tool_calls(tool_call), None))

        interrupt_mock.assert_called_once()
        payload = interrupt_mock.call_args.args[0]
        self.assertTrue(payload.get("_approval"))
        requests = payload["requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["tool_name"], "mcp__dynamic_tool")
        self.assertEqual(requests[0]["risk_level"], RiskLevel.CONTROLLED.value)
        audit_mock.assert_not_called()

    def test_whitelist_tool_auto_approved_without_interrupt(self):
        """白名单工具（如 calculator）SAFE 自动通过，仅审计不审批。"""
        mw = self._middleware()
        tool_call = {
            "name": "calculator",
            "args": {"expression": "1+1"},
            "id": "tc-calc-1",
            "type": "tool_call",
        }
        with (
            patch.object(
                mw, "_build_idempotent_tc_ids", new=AsyncMock(return_value=(set(), {}))
            ),
            patch.object(mw, "_audit_auto_approved_tools", new=AsyncMock()) as audit_mock,
            patch(
                "Django_xm.apps.agent_hub.approval.middleware.interrupt"
            ) as interrupt_mock,
        ):
            result = run_async(mw.aafter_model(_state_with_tool_calls(tool_call), None))

        interrupt_mock.assert_not_called()
        audit_mock.assert_called_once()
        self.assertEqual(audit_mock.call_args.args[0][0]["tool_name"], "calculator")
        self.assertIsNone(result)

    def test_system_orchestration_tool_exempt_from_approval(self):
        """wait_for_subagent 保持免审批（注册 ctx + PENDING），不进审批批次。"""
        self.assertIn("wait_for_subagent", SYSTEM_ORCHESTRATION_TOOLS)
        mw = self._middleware()
        tool_call = {
            "name": "wait_for_subagent",
            "args": {"subagent_thread_ids": ["sub-1"]},
            "id": "tc-wait-1",
            "type": "tool_call",
        }
        with (
            patch.object(
                mw, "_build_idempotent_tc_ids", new=AsyncMock(return_value=(set(), {}))
            ),
            patch.object(mw, "_audit_auto_approved_tools", new=AsyncMock()) as audit_mock,
            patch(
                "Django_xm.apps.agent_hub.approval.middleware.interrupt"
            ) as interrupt_mock,
        ):
            result = run_async(mw.aafter_model(_state_with_tool_calls(tool_call), None))

        interrupt_mock.assert_not_called()
        audit_mock.assert_called_once()
        # 编排工具保持 PENDING 发布语义（publish_running=False, publish_pending=True）
        self.assertFalse(audit_mock.call_args.kwargs.get("publish_running", True))
        self.assertTrue(audit_mock.call_args.kwargs.get("publish_pending", False))
        self.assertIsNone(result)


class TestSyncAfterModelFailClosed(SimpleTestCase):
    """2.2：同步 after_model 携带 tool_calls 时 fail-closed 抛异常。"""

    def test_sync_with_tool_calls_raises(self):
        mw = ApprovalMiddleware()
        tool_call = {"name": "shell_exec", "args": {"command": "ls"}, "id": "tc-1"}
        with self.assertRaises(SyncApprovalNotSupportedError):
            mw.after_model(_state_with_tool_calls(tool_call), None)

    def test_sync_without_tool_calls_passes(self):
        """纯文本回复轮（无工具执行）无需审批，正常放行。"""
        mw = ApprovalMiddleware()
        state = {"messages": [AIMessage(content="你好")]}
        self.assertIsNone(mw.after_model(state, None))


class TestIdempotencyFailureFailClosed(SimpleTestCase):
    """2.5：幂等集合构建失败时，SAFE 级工具也强制送审。"""

    def test_build_idempotent_returns_none_sentinel_on_error(self):
        mw = ApprovalMiddleware()
        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "ls", "args": {}, "id": "tc-ls-1"}],
        )
        with patch(
            "Django_xm.apps.agent_hub.approval.middleware.sync_to_async",
            side_effect=RuntimeError("db unavailable"),
        ):
            approved, decided = run_async(mw._build_idempotent_tc_ids(ai_msg))
        self.assertIsNone(approved)
        self.assertIsNone(decided)

    def test_safe_tool_escalated_when_idempotency_fails(self):
        mw = ApprovalMiddleware()
        tool_call = {
            "name": "ls",
            "args": {"path": "."},
            "id": "tc-ls-2",
            "type": "tool_call",
        }
        with patch(
            "Django_xm.apps.agent_hub.approval.middleware.sync_to_async",
            side_effect=RuntimeError("db unavailable"),
        ):
            with patch.object(
                mw, "_audit_auto_approved_tools", new=AsyncMock()
            ) as audit_mock:
                with patch(
                    "Django_xm.apps.agent_hub.approval.middleware.interrupt",
                    return_value=False,
                ) as interrupt_mock:
                    run_async(mw.aafter_model(_state_with_tool_calls(tool_call), None))

        interrupt_mock.assert_called_once()
        requests = interrupt_mock.call_args.args[0]["requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["tool_name"], "ls")
        # SAFE 升级为 CONTROLLED 送审（fail-closed）
        self.assertEqual(requests[0]["risk_level"], RiskLevel.CONTROLLED.value)
        audit_mock.assert_not_called()


class TestOrchestrationExemptListGuard(SimpleTestCase):
    """守护：wait_for_subagent 之外的编排豁免清单保持封闭（防滥用为逃生门）。"""

    def test_orchestration_exempt_list_is_closed(self):
        self.assertEqual(SYSTEM_ORCHESTRATION_TOOLS, frozenset({"wait_for_subagent"}))


if __name__ == "__main__":
    unittest.main()
