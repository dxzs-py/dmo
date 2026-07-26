"""approval_service 单元测试 - 补充 Task 14.4 用例。

覆盖 spec `unify-approval-and-timeout-recovery` 阶段四变更：
1. _finalize_waiting_siblings_sync 在 sibling 终态为 timeout 时调用 _publish_tool_call_timeout_event
2. complete_approval 调用时会清理同批次 waiting siblings

运行方式:
    cd d:\programming\langchain\langchain_xm\backend\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.approvals.tests.test_approval_service_sibling_cleanup --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch, MagicMock

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service


def _make_approval(**overrides):
    """创建测试用 Approval 记录（与 test_approval_service.py 保持一致）。"""
    defaults = {
        'interrupt_id': 'test-interrupt-id',
        'source': Approval.SOURCE_CHAT,
        'source_id': 'test-source-id',
        'chat_session_id': 'test-chat-session-id',
        'tool_name': 'shell_exec',
        'title': '确认执行',
        'description': '执行 shell 命令',
        'action': Approval.ACTION_CONFIRM,
        'operation': 'rm -rf /tmp/test',
        'danger_level': 'high',
        'parameters': {'command': 'rm -rf /tmp/test'},
        'state': Approval.STATE_PENDING,
        'extra': {},
    }
    defaults.update(overrides)
    return Approval.objects.create(**defaults)


class FinalizeWaitingSiblingsTimeoutTests(TestCase):
    """_finalize_waiting_siblings_sync 在 sibling 终态为 timeout 时发布 TOOL_CALL_TIMEOUT 事件。"""

    def setUp(self):
        Approval.objects.all().delete()

    @patch('Django_xm.apps.approvals.services.approval_service.sync_approval_state_to_chat_message')
    @patch('Django_xm.apps.approvals.services.approval_service._publish_tool_call_timeout_event')
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_finalize_waiting_siblings_timeout_publishes_event(
        self,
        mock_get_redis,
        mock_persist,
        mock_publish,
        mock_publish_timeout,
        mock_sync_chat,
    ):
        """同批次 2 个 approval：A=timeout 触发，B=waiting sibling，
        终态化 B 为 timeout 时应调用 _publish_tool_call_timeout_event。"""
        # 触发审批：终态 timeout
        trigger = _make_approval(
            interrupt_id='trigger-1',
            state=Approval.STATE_TIMEOUT,
            extra={'graph_interrupt_id': 'gid-test-1'},
        )
        # sibling 审批：waiting 状态，extra._approved=False → 终态 rejected
        # 但若设 extra._approved 缺失，则 final_state = trigger.state = timeout
        sibling = _make_approval(
            interrupt_id='sibling-1',
            state=Approval.STATE_WAITING,
            extra={'graph_interrupt_id': 'gid-test-1'},  # 无 _approved 字段，回退到 trigger.state
        )

        cleaned = approval_service._finalize_waiting_siblings_sync(
            trigger, 'gid-test-1'
        )

        self.assertEqual(cleaned, 1)
        # sibling 终态为 timeout（与 trigger 一致）
        sibling.refresh_from_db()
        self.assertEqual(sibling.state, Approval.STATE_TIMEOUT)
        # 验证调用了 _publish_tool_call_timeout_event
        mock_publish_timeout.assert_called_once()
        call_args = mock_publish_timeout.call_args
        # 第一个位置参数为 sibling Approval 实例
        self.assertEqual(call_args.args[0].interrupt_id, 'sibling-1')

    @patch('Django_xm.apps.approvals.services.approval_service.sync_approval_state_to_chat_message')
    @patch('Django_xm.apps.approvals.services.approval_service._publish_tool_call_timeout_event')
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_finalize_waiting_siblings_approved_does_not_publish_timeout(
        self,
        mock_get_redis,
        mock_persist,
        mock_publish,
        mock_publish_timeout,
        mock_sync_chat,
    ):
        """sibling 终态为 approved 时不应调用 _publish_tool_call_timeout_event。"""
        trigger = _make_approval(
            interrupt_id='trigger-2',
            state=Approval.STATE_APPROVED,
            extra={'graph_interrupt_id': 'gid-test-2'},
        )
        # sibling: _approved=True → 终态 approved
        sibling = _make_approval(
            interrupt_id='sibling-2',
            state=Approval.STATE_WAITING,
            extra={'graph_interrupt_id': 'gid-test-2', '_approved': True},
        )

        cleaned = approval_service._finalize_waiting_siblings_sync(
            trigger, 'gid-test-2'
        )

        self.assertEqual(cleaned, 1)
        sibling.refresh_from_db()
        self.assertEqual(sibling.state, Approval.STATE_APPROVED)
        # 不应调用 timeout 事件
        mock_publish_timeout.assert_not_called()

    @patch('Django_xm.apps.approvals.services.approval_service.sync_approval_state_to_chat_message')
    @patch('Django_xm.apps.approvals.services.approval_service._publish_tool_call_timeout_event')
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_finalize_waiting_siblings_no_siblings_returns_zero(
        self,
        mock_get_redis,
        mock_persist,
        mock_publish,
        mock_publish_timeout,
        mock_sync_chat,
    ):
        """无 waiting sibling 时返回 0，不发布任何事件。"""
        trigger = _make_approval(
            interrupt_id='trigger-3',
            state=Approval.STATE_TIMEOUT,
            extra={'graph_interrupt_id': 'gid-test-3'},
        )
        # 无 sibling

        cleaned = approval_service._finalize_waiting_siblings_sync(
            trigger, 'gid-test-3'
        )

        self.assertEqual(cleaned, 0)
        mock_publish_timeout.assert_not_called()


class CompleteApprovalSiblingCleanupTests(TestCase):
    """complete_approval 触发同批次 waiting siblings 清理。"""

    def setUp(self):
        Approval.objects.all().delete()

    @patch('Django_xm.apps.approvals.services.approval_service.sync_approval_state_to_chat_message')
    @patch('Django_xm.apps.approvals.services.approval_service._finalize_waiting_siblings_sync')
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_complete_approval_triggers_sibling_cleanup(
        self,
        mock_get_redis,
        mock_persist,
        mock_publish,
        mock_finalize_siblings,
        mock_sync_chat,
    ):
        """complete_approval 调用时会触发 _finalize_waiting_siblings_sync 清理同批次 waiting。"""
        approval = _make_approval(
            interrupt_id='complete-1',
            state=Approval.STATE_PROCESSING,
            extra={'graph_interrupt_id': 'gid-complete-1', '_resume_value': True, '_approved': True},
        )

        approval_service.complete_approval(
            interrupt_id='complete-1',
            state=Approval.STATE_APPROVED,
        )

        # 验证调用了 _finalize_waiting_siblings_sync
        mock_finalize_siblings.assert_called_once()
        call_args = mock_finalize_siblings.call_args
        # 第一个位置参数为 approval 实例，第二个为 graph_interrupt_id
        self.assertEqual(call_args.args[0].interrupt_id, 'complete-1')
        self.assertEqual(call_args.args[1], 'gid-complete-1')

        # 验证 approval 已终态化
        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_APPROVED)
        self.assertIsNotNone(approval.resolved_at)


if __name__ == "__main__":
    unittest.main()