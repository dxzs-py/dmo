"""ApprovalGateway 统一审批路由网关单元测试。

覆盖：
- route_resume 路由分流（chat→SSE / deep_research→Celery / 未知→ValueError）
- _is_high_risk_approval 风险判定（risk_level 优先 + danger_level 回退）
- _check_circuit_breaker F3 高频高危熔断（阈值/跳过条件）
- _resume_deep_research Celery 任务派发参数
"""

import unittest
from unittest.mock import MagicMock, patch

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.common.approval_gateway import (
    ApprovalGateway,
    CircuitBreakerError,
    gateway,
)


def _make_approval(**overrides):
    """创建测试用 Approval 记录。"""
    defaults = {
        "interrupt_id": "gw-test-interrupt",
        "source": Approval.SOURCE_CHAT,
        "source_id": "gw-test-session",
        "chat_session_id": "gw-test-session",
        "tool_name": "shell_exec",
        "title": "确认执行",
        "description": "执行命令",
        "action": Approval.ACTION_CONFIRM,
        "operation": "rm -rf /tmp/x",
        "danger_level": "high",
        "parameters": {"command": "rm -rf /tmp/x"},
        "state": Approval.STATE_PENDING,
        "extra": {},
    }
    defaults.update(overrides)
    return Approval.objects.create(**defaults)


class RouteResumeTests(TestCase):
    """route_resume 路由分流测试。"""

    def setUp(self):
        Approval.objects.all().delete()

    @patch.object(ApprovalGateway, "_resume_chat")
    def test_chat_source_routes_to_sse(self, mock_resume_chat):
        """chat source → _resume_chat 返回 SSE 生成器（非 None）。"""
        sentinel = object()
        mock_resume_chat.return_value = sentinel
        approval = _make_approval(source=Approval.SOURCE_CHAT, danger_level="medium")
        request = MagicMock()

        result = gateway.route_resume(request, approval, resume_value=True, approved=True)

        self.assertIs(result, sentinel)
        mock_resume_chat.assert_called_once()

    @patch.object(ApprovalGateway, "_resume_deep_research")
    def test_deep_research_source_routes_to_celery(self, mock_resume_dr):
        """deep_research source → _resume_deep_research 返回 None。"""
        mock_resume_dr.return_value = None
        approval = _make_approval(
            source=Approval.SOURCE_DEEP_RESEARCH,
            danger_level="medium",
            source_id="dr-task-1",
        )
        request = MagicMock()

        result = gateway.route_resume(request, approval, resume_value=True, approved=True)

        self.assertIsNone(result)
        mock_resume_dr.assert_called_once()

    def test_unknown_source_raises_value_error(self):
        """未知 source → ValueError。"""
        approval = _make_approval(source="unknown_source", danger_level="medium")
        request = MagicMock()

        with self.assertRaises(ValueError):
            gateway.route_resume(request, approval, resume_value=True, approved=True)


class IsHighRiskApprovalTests(unittest.TestCase):
    """_is_high_risk_approval 风险判定测试（静态方法，无需 DB）。"""

    def _make_mock(self, extra=None, danger_level="medium"):
        approval = MagicMock()
        approval.extra = extra if extra is not None else {}
        approval.danger_level = danger_level
        return approval

    def test_high_by_risk_level(self):
        """extra.risk_level=high → True（新标准优先于 danger_level）。"""
        approval = self._make_mock(extra={"risk_level": "high"}, danger_level="medium")
        self.assertTrue(ApprovalGateway._is_high_risk_approval(approval))

    def test_high_by_danger_level_fallback(self):
        """无 risk_level 但 danger_level=high → True（legacy 回退）。"""
        approval = self._make_mock(extra={}, danger_level="high")
        self.assertTrue(ApprovalGateway._is_high_risk_approval(approval))

    def test_not_high_risk_controlled(self):
        """risk_level=controlled → False。"""
        approval = self._make_mock(extra={"risk_level": "controlled"}, danger_level="medium")
        self.assertFalse(ApprovalGateway._is_high_risk_approval(approval))

    def test_not_high_risk_safe(self):
        """risk_level=safe → False。"""
        approval = self._make_mock(extra={"risk_level": "safe"}, danger_level="low")
        self.assertFalse(ApprovalGateway._is_high_risk_approval(approval))

    def test_invalid_risk_level_falls_back_to_danger_level(self):
        """无效 risk_level 字符串 → 回退到 danger_level 判定。"""
        approval = self._make_mock(extra={"risk_level": "invalid_value"}, danger_level="high")
        self.assertTrue(ApprovalGateway._is_high_risk_approval(approval))

    def test_none_extra_treated_as_empty(self):
        """extra=None → 视为空 dict，回退到 danger_level。"""
        approval = MagicMock()
        approval.extra = None
        approval.danger_level = "high"
        self.assertTrue(ApprovalGateway._is_high_risk_approval(approval))


class CircuitBreakerTests(TestCase):
    """F3 高频高危熔断测试。

    熔断触发条件：approved=True AND _is_high_risk_approval=True AND
                  get_high_risk_window_count(source_id) > HIGH_RISK_WINDOW_THRESHOLD(10)。
    """

    def setUp(self):
        Approval.objects.all().delete()

    @patch("Django_xm.common.approval_gateway.approval_metrics")
    @patch.object(ApprovalGateway, "_is_high_risk_approval", return_value=True)
    def test_circuit_breaker_triggers_when_over_threshold(self, mock_high, mock_metrics):
        """approved=True + HIGH 级 + 窗口数(11) > 阈值(10) → CircuitBreakerError。"""
        mock_metrics.get_high_risk_window_count.return_value = 11
        approval = _make_approval(danger_level="high", source_id="cb-session")
        request = MagicMock()

        with self.assertRaises(CircuitBreakerError) as ctx:
            gateway.route_resume(request, approval, resume_value=True, approved=True)

        self.assertEqual(ctx.exception.source_id, "cb-session")
        self.assertEqual(ctx.exception.window_count, 11)
        self.assertEqual(ctx.exception.threshold, 10)
        mock_metrics.get_high_risk_window_count.assert_called_once_with("cb-session")

    @patch("Django_xm.common.approval_gateway.approval_metrics")
    @patch.object(ApprovalGateway, "_is_high_risk_approval", return_value=True)
    @patch.object(ApprovalGateway, "_resume_chat", return_value=object())
    def test_circuit_breaker_not_triggered_at_threshold(self, mock_resume, mock_high, mock_metrics):
        """窗口数(10) == 阈值(10) → 不熔断（> 严格大于），正常路由。"""
        mock_metrics.get_high_risk_window_count.return_value = 10
        approval = _make_approval(danger_level="high", source_id="cb-session")
        request = MagicMock()

        result = gateway.route_resume(request, approval, resume_value=True, approved=True)
        self.assertIsNotNone(result)

    @patch("Django_xm.common.approval_gateway.approval_metrics")
    @patch.object(ApprovalGateway, "_is_high_risk_approval", return_value=True)
    def test_circuit_breaker_skipped_when_rejected(self, mock_high, mock_metrics):
        """approved=False → 不触发熔断检查（即使 HIGH 级且窗口超阈值）。"""
        mock_metrics.get_high_risk_window_count.return_value = 999  # 远超阈值
        approval = _make_approval(
            source=Approval.SOURCE_DEEP_RESEARCH,
            danger_level="high",
            source_id="cb-task",
        )
        request = MagicMock()

        with patch.object(ApprovalGateway, "_resume_deep_research", return_value=None):
            result = gateway.route_resume(request, approval, resume_value=False, approved=False)

        self.assertIsNone(result)
        mock_metrics.get_high_risk_window_count.assert_not_called()

    @patch("Django_xm.common.approval_gateway.approval_metrics")
    @patch.object(ApprovalGateway, "_is_high_risk_approval", return_value=False)
    @patch.object(ApprovalGateway, "_resume_chat", return_value=object())
    def test_circuit_breaker_skipped_when_not_high_risk(self, mock_resume, mock_high, mock_metrics):
        """非 HIGH 级 → 不触发熔断检查。"""
        approval = _make_approval(danger_level="medium", source_id="cb-session")
        request = MagicMock()

        result = gateway.route_resume(request, approval, resume_value=True, approved=True)
        self.assertIsNotNone(result)
        mock_metrics.get_high_risk_window_count.assert_not_called()

    @patch("Django_xm.common.approval_gateway.approval_metrics")
    @patch.object(ApprovalGateway, "_is_high_risk_approval", return_value=True)
    @patch.object(ApprovalGateway, "_resume_chat", return_value=object())
    def test_circuit_breaker_skipped_without_source_id(self, mock_resume, mock_high, mock_metrics):
        """无 source_id → _check_circuit_breaker 内部 return，跳过检查。"""
        mock_metrics.get_high_risk_window_count.return_value = 999
        approval = _make_approval(danger_level="high", source_id="")
        request = MagicMock()

        result = gateway.route_resume(request, approval, resume_value=True, approved=True)
        # source_id 为空，_check_circuit_breaker 直接 return，不抛 CircuitBreakerError
        self.assertIsNotNone(result)


class ResumeDeepResearchTests(unittest.TestCase):
    """_resume_deep_research Celery 任务派发测试（纯 mock，无需 DB）。"""

    @patch("Django_xm.tasks.deep_research.research_resume_task")
    def test_dispatches_celery_task_with_correct_args(self, mock_task):
        """_resume_deep_research 调用 research_resume_task.delay 并返回 None。"""
        approval = MagicMock()
        approval.source_id = "dr-task-123"
        approval.interrupt_id = "dr-interrupt"
        approval.user_id = 42
        approval.chat_session_id = "chat-sess"
        approval.extra = {
            "graph_interrupt_id": "gi-123",
            "langgraph_resume_id": "lg-456",
            "message_id": "msg-789",
        }

        gw = ApprovalGateway()
        result = gw._resume_deep_research(
            approval,
            resume_value=True,
            graph_interrupt_id=None,
            langgraph_resume_id=None,
            approved=True,
        )

        self.assertIsNone(result)
        mock_task.delay.assert_called_once()
        call_kwargs = mock_task.delay.call_args.kwargs
        self.assertEqual(call_kwargs["thread_id"], "dr-task-123")
        self.assertEqual(call_kwargs["interrupt_id"], "dr-interrupt")
        self.assertEqual(call_kwargs["user_id"], 42)
        self.assertEqual(call_kwargs["graph_interrupt_id"], "gi-123")
        self.assertEqual(call_kwargs["langgraph_resume_id"], "lg-456")
        self.assertEqual(call_kwargs["message_id"], "msg-789")
        self.assertEqual(call_kwargs["chat_session_id"], "chat-sess")
        self.assertTrue(call_kwargs["resume_value"])

    @patch("Django_xm.tasks.deep_research.research_resume_task")
    def test_falls_back_to_interrupt_id_when_no_langgraph_resume_id(self, mock_task):
        """langgraph_resume_id 缺失时回退到 approval.interrupt_id。"""
        approval = MagicMock()
        approval.source_id = "dr-task"
        approval.interrupt_id = "dr-interrupt"
        approval.user_id = 1
        approval.chat_session_id = ""
        approval.extra = {}  # 无 langgraph_resume_id / graph_interrupt_id / message_id

        gw = ApprovalGateway()
        gw._resume_deep_research(
            approval,
            resume_value=False,
            graph_interrupt_id=None,
            langgraph_resume_id=None,
            approved=False,
        )

        call_kwargs = mock_task.delay.call_args.kwargs
        self.assertEqual(call_kwargs["langgraph_resume_id"], "dr-interrupt")
        self.assertEqual(call_kwargs["graph_interrupt_id"], "")
        self.assertEqual(call_kwargs["message_id"], "")
