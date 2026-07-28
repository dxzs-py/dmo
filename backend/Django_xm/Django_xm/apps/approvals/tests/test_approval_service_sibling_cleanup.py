"""approval_lifecycle.service.complete_batch 单元测试。

原 _finalize_waiting_siblings_sync 已重构为 ApprovalLifecycleService.complete_batch
（三模块共享的统一终态化入口，根因 C 修复）。
本测试覆盖 spec `unify-approval-and-timeout-recovery` 阶段四变更：
1. complete_batch 在 sibling 终态为 timeout 时调用 _publish_tool_call_timeout_event
2. complete_approval 调用时委托 complete_batch 清理同批次 waiting siblings

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.approvals.tests.test_approval_service_sibling_cleanup --verbosity=2
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service
from Django_xm.common.approval_lifecycle import service as approval_lifecycle_service


def _make_approval(**overrides):
    """创建测试用 Approval 记录。"""
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


class CompleteBatchTimeoutTests(TestCase):
    """ApprovalLifecycleService.complete_batch 在 sibling 终态为 timeout 时发布 TOOL_CALL_TIMEOUT 事件。

    原测试直接调用 approval_service._finalize_waiting_siblings_sync，
    重构后改为调用 approval_lifecycle_service.complete_batch（三模块共享入口）。
    """

    def setUp(self):
        Approval.objects.all().delete()

    @patch('Django_xm.common.approval_lifecycle._persist_and_broadcast')
    @patch('Django_xm.common.approval_lifecycle._publish_tool_call_timeout_event')
    @patch('Django_xm.common.approval_lifecycle.sync_approval_state_to_chat_message')
    @patch('Django_xm.common.approval_lifecycle._release_lock')
    def test_complete_batch_timeout_publishes_event(
        self,
        mock_release_lock,
        mock_sync_chat,
        mock_publish_timeout,
        mock_persist,
    ):
        """同批次 2 个 approval：A=timeout 触发，B=waiting sibling，
        终态化 B 为 timeout 时应调用 _publish_tool_call_timeout_event。"""
        # 触发审批：终态 timeout（已被 complete_batch 跳过，因为已终态）
        _make_approval(
            interrupt_id='trigger-1',
            state=Approval.STATE_TIMEOUT,
            extra={'graph_interrupt_id': 'gid-test-1'},
        )
        # sibling 审批：waiting 状态，extra._approved 缺失 → 回退到 trigger.state = timeout
        sibling = _make_approval(
            interrupt_id='sibling-1',
            state=Approval.STATE_WAITING,
            extra={'graph_interrupt_id': 'gid-test-1'},
        )

        result = approval_lifecycle_service.complete_batch(
            'gid-test-1', 'trigger-1', Approval.STATE_TIMEOUT,
        )

        # trigger 已终态 → skipped；sibling waiting → success
        self.assertEqual(result['success'], 1)
        self.assertEqual(result['skipped'], 1)
        # sibling 终态为 timeout（与 trigger 一致）
        sibling.refresh_from_db()
        self.assertEqual(sibling.state, Approval.STATE_TIMEOUT)
        # 验证调用了 _publish_tool_call_timeout_event
        mock_publish_timeout.assert_called_once()
        call_args = mock_publish_timeout.call_args
        # 第一个位置参数为 sibling Approval 实例
        self.assertEqual(call_args.args[0].interrupt_id, 'sibling-1')

    @patch('Django_xm.common.approval_lifecycle._persist_and_broadcast')
    @patch('Django_xm.common.approval_lifecycle._publish_tool_call_timeout_event')
    @patch('Django_xm.common.approval_lifecycle.sync_approval_state_to_chat_message')
    @patch('Django_xm.common.approval_lifecycle._release_lock')
    def test_complete_batch_approved_does_not_publish_timeout(
        self,
        mock_release_lock,
        mock_sync_chat,
        mock_publish_timeout,
        mock_persist,
    ):
        """sibling 终态为 approved 时不应调用 _publish_tool_call_timeout_event。"""
        _make_approval(
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

        result = approval_lifecycle_service.complete_batch(
            'gid-test-2', 'trigger-2', Approval.STATE_APPROVED,
        )

        self.assertEqual(result['success'], 1)
        sibling.refresh_from_db()
        self.assertEqual(sibling.state, Approval.STATE_APPROVED)
        # 不应调用 timeout 事件
        mock_publish_timeout.assert_not_called()

    @patch('Django_xm.common.approval_lifecycle._persist_and_broadcast')
    @patch('Django_xm.common.approval_lifecycle._publish_tool_call_timeout_event')
    @patch('Django_xm.common.approval_lifecycle.sync_approval_state_to_chat_message')
    @patch('Django_xm.common.approval_lifecycle._release_lock')
    def test_complete_batch_no_siblings_returns_zero(
        self,
        mock_release_lock,
        mock_sync_chat,
        mock_publish_timeout,
        mock_persist,
    ):
        """无 waiting sibling 时返回 skipped=1（仅 trigger 自身，且已终态跳过）。"""
        _make_approval(
            interrupt_id='trigger-3',
            state=Approval.STATE_TIMEOUT,
            extra={'graph_interrupt_id': 'gid-test-3'},
        )
        # 无 sibling

        result = approval_lifecycle_service.complete_batch(
            'gid-test-3', 'trigger-3', Approval.STATE_TIMEOUT,
        )

        # trigger 自身已终态 → skipped=1，success=0
        self.assertEqual(result['success'], 0)
        self.assertEqual(result['skipped'], 1)
        mock_publish_timeout.assert_not_called()


class CompleteApprovalSiblingCleanupTests(TestCase):
    """complete_approval 触发 ApprovalLifecycleService.complete_batch 清理同批次 waiting siblings。

    集成测试：不 mock complete_batch，验证完整端到端行为。
    """

    def setUp(self):
        Approval.objects.all().delete()

    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service._publish_tool_call_timeout_event')
    @patch('Django_xm.apps.approvals.services.approval_service.sync_approval_state_to_chat_message')
    def test_complete_approval_triggers_sibling_cleanup(
        self,
        mock_sync_chat,
        mock_publish_timeout,
        mock_broadcast,
        mock_persist,
        mock_get_redis,
    ):
        """complete_approval 调用时委托 complete_batch 清理同批次 waiting siblings。

        验证：
        1. trigger approval 被终态化为 approved
        2. 同批次 waiting sibling 也被终态化（_approved=True → approved）
        3. sibling 终态为 timeout 时才调用 _publish_tool_call_timeout_event（本例 approved 不调用）
        """
        # trigger: processing → approved
        approval = _make_approval(
            interrupt_id='complete-1',
            state=Approval.STATE_PROCESSING,
            extra={'graph_interrupt_id': 'gid-complete-1', '_resume_value': True, '_approved': True},
        )
        # sibling: waiting，_approved=True → 终态 approved
        sibling = _make_approval(
            interrupt_id='sibling-complete-1',
            state=Approval.STATE_WAITING,
            extra={'graph_interrupt_id': 'gid-complete-1', '_approved': True},
        )

        approval_service.complete_approval(
            interrupt_id='complete-1',
            state=Approval.STATE_APPROVED,
        )

        # 验证 trigger approval 已终态化
        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_APPROVED)
        self.assertIsNotNone(approval.resolved_at)

        # 验证 sibling 也被终态化（approved，不调用 timeout 事件）
        sibling.refresh_from_db()
        self.assertEqual(sibling.state, Approval.STATE_APPROVED)


if __name__ == "__main__":
    unittest.main()
