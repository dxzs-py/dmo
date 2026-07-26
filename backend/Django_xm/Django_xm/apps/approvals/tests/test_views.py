"""审批视图 API 测试。

测试 ApprovalListView、ApprovalDetailView、ApprovalResumeView、ApprovalRejectView
四个视图的请求与响应行为，mock 掉 Redis/Celery/SSE 等外部依赖。
"""

from unittest.mock import patch
import unittest

from django.http import StreamingHttpResponse
from rest_framework.test import APITestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.users.models import User


class ApprovalViewTestBase(APITestCase):
    """审批视图测试公共 setUp。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='approval-view-tester',
            password='testpass123',
        )

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def _make_approval(**kwargs):
        """创建一条 Approval 记录，提供合理默认值。"""
        defaults = {
            'interrupt_id': 'test-interrupt-001',
            'source': Approval.SOURCE_CHAT,
            'source_id': 'test-session-001',
            'chat_session_id': 'test-session-001',
            'tool_name': 'shell_exec',
            'title': '确认执行',
            'description': '执行 shell 命令',
            'action': Approval.ACTION_CONFIRM,
            'operation': 'rm -rf /tmp/test',
            'danger_level': 'high',
            'parameters': {'command': 'rm -rf /tmp/test'},
            'state': Approval.STATE_PENDING,
        }
        defaults.update(kwargs)
        return Approval.objects.create(**defaults)


class ApprovalListViewTests(ApprovalViewTestBase):
    """ApprovalListView 测试：GET /api/v1/approvals/。"""

    def setUp(self):
        super().setUp()
        self.approval1 = self._make_approval(
            interrupt_id='int-001',
            source_id='src-001',
            chat_session_id='sess-001',
            state=Approval.STATE_PENDING,
        )
        self.approval2 = self._make_approval(
            interrupt_id='int-002',
            source_id='src-002',
            chat_session_id='sess-002',
            state=Approval.STATE_APPROVED,
        )
        self.approval3 = self._make_approval(
            interrupt_id='int-003',
            source_id='src-001',
            chat_session_id='sess-001',
            state=Approval.STATE_REJECTED,
        )

    def test_filter_by_source_id(self):
        """GET ?source_id=xxx 返回过滤结果。"""
        resp = self.client.get('/api/v1/approvals/', {'source_id': 'src-001'})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['code'], 200)
        interrupt_ids = [item['interrupt_id'] for item in body['data']]
        self.assertIn('int-001', interrupt_ids)
        self.assertIn('int-003', interrupt_ids)
        self.assertNotIn('int-002', interrupt_ids)

    def test_filter_by_chat_session_id(self):
        """GET ?chat_session_id=xxx 返回过滤结果。"""
        resp = self.client.get('/api/v1/approvals/', {'chat_session_id': 'sess-002'})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        interrupt_ids = [item['interrupt_id'] for item in body['data']]
        self.assertEqual(interrupt_ids, ['int-002'])

    def test_filter_by_state(self):
        """GET ?state=pending 返回过滤结果。"""
        resp = self.client.get('/api/v1/approvals/', {'state': 'pending'})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        interrupt_ids = [item['interrupt_id'] for item in body['data']]
        self.assertIn('int-001', interrupt_ids)
        self.assertNotIn('int-002', interrupt_ids)
        self.assertNotIn('int-003', interrupt_ids)

    def test_unauthenticated_returns_401(self):
        """未认证请求返回 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.get('/api/v1/approvals/')
        self.assertEqual(resp.status_code, 401)


class ApprovalDetailViewTests(ApprovalViewTestBase):
    """ApprovalDetailView 测试：GET /api/v1/approvals/{interrupt_id}/。"""

    def setUp(self):
        super().setUp()
        self.approval = self._make_approval(
            interrupt_id='detail-001',
            source_id='src-detail',
        )

    def test_get_detail_success(self):
        """存在的 interrupt_id 返回详情。"""
        resp = self.client.get(f'/api/v1/approvals/{self.approval.interrupt_id}/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['code'], 200)
        self.assertEqual(body['data']['interrupt_id'], 'detail-001')
        self.assertEqual(body['data']['tool_name'], 'shell_exec')

    def test_get_detail_not_found(self):
        """不存在的 interrupt_id 返回 404。"""
        resp = self.client.get('/api/v1/approvals/nonexistent-id/')
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body['code'], 40401)


class ApprovalResumeViewTests(ApprovalViewTestBase):
    """ApprovalResumeView 测试：POST /api/v1/approvals/{interrupt_id}/resume/。"""

    def setUp(self):
        super().setUp()
        self.chat_approval = self._make_approval(
            interrupt_id='resume-chat-001',
            source=Approval.SOURCE_CHAT,
            source_id='chat-session-001',
            chat_session_id='chat-session-001',
        )
        self.research_approval = self._make_approval(
            interrupt_id='resume-research-001',
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id='research-task-001',
            chat_session_id='chat-session-001',
        )

    @unittest.skip('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')
    @patch('Django_xm.apps.chat.services.chat_resume_service.stream_chat_resume_response')
    @patch('Django_xm.apps.approvals.views.approval_service.resume_approval')
    def test_resume_chat_source_returns_sse_stream(self, mock_resume, mock_stream):
        """chat source 返回 SSE 流。"""
        mock_resume.return_value = {
            'approval': self.chat_approval,
            'resume_value': True,
        }
        fake_response = StreamingHttpResponse(
            streaming_content=iter(['data: test\n\n']),
            content_type='text/event-stream',
        )
        mock_stream.return_value = fake_response

        resp = self.client.post(
            f'/api/v1/approvals/{self.chat_approval.interrupt_id}/resume/',
            {'approved': True},
            format='json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/event-stream', resp['Content-Type'])
        mock_resume.assert_called_once_with(
            interrupt_id='resume-chat-001',
            approved=True,
            user_input=None,
        )
        mock_stream.assert_called_once()
        # 校验 session_id 取自 chat_session_id，approved=True
        call_args = mock_stream.call_args
        self.assertEqual(call_args.args[3], 'chat-session-001')
        self.assertTrue(call_args.kwargs.get('approved', False))

    @unittest.skip('_resume_research 已在 Phase 1 移除，相关测试待后续 Phase 重建')
    @patch('Django_xm.apps.approvals.views.approval_service._resume_research')
    @patch('Django_xm.apps.approvals.views.approval_service.resume_approval')
    def test_resume_deep_research_source_returns_202(self, mock_resume, mock_resume_research):
        """deep_research source 返回 202。"""
        mock_resume.return_value = {
            'approval': self.research_approval,
            'resume_value': True,
        }

        resp = self.client.post(
            f'/api/v1/approvals/{self.research_approval.interrupt_id}/resume/',
            {'approved': True},
            format='json',
        )

        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body['code'], 0)
        self.assertEqual(body['data']['state'], 'processing')
        self.assertEqual(body['data']['interrupt_id'], 'resume-research-001')
        mock_resume_research.assert_called_once_with(
            task_id='research-task-001',
            interrupt_id='resume-research-001',
            resume_value=True,
        )

    @patch('Django_xm.apps.approvals.views.approval_service.resume_approval')
    def test_resume_not_found_returns_error(self, mock_resume):
        """审批不存在时返回错误。"""
        mock_resume.side_effect = ValueError('审批记录不存在: interrupt_id=nonexistent')

        resp = self.client.post('/api/v1/approvals/nonexistent/resume/')

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body['code'], 40002)
        self.assertIn('审批记录不存在', body['message'])

    @patch('Django_xm.apps.approvals.views.approval_service.resume_approval')
    def test_resume_non_pending_returns_error(self, mock_resume):
        """审批状态非 pending 时返回错误。"""
        mock_resume.side_effect = ValueError('审批状态非 pending，无法恢复: state=approved')

        resp = self.client.post(
            f'/api/v1/approvals/{self.chat_approval.interrupt_id}/resume/'
        )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body['code'], 40002)
        self.assertIn('审批状态非 pending', body['message'])


class ApprovalRejectViewTests(ApprovalViewTestBase):
    """ApprovalRejectView 测试：POST /api/v1/approvals/{interrupt_id}/reject/。"""

    def setUp(self):
        super().setUp()
        self.chat_approval = self._make_approval(
            interrupt_id='reject-chat-001',
            source=Approval.SOURCE_CHAT,
            source_id='chat-session-002',
            chat_session_id='chat-session-002',
        )
        self.research_approval = self._make_approval(
            interrupt_id='reject-research-001',
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id='research-task-002',
            chat_session_id='chat-session-002',
        )

    @unittest.skip('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')
    @patch('Django_xm.apps.chat.services.chat_resume_service.stream_chat_resume_response')
    @patch('Django_xm.apps.approvals.views.approval_service.resume_approval')
    def test_reject_chat_source_returns_sse_stream(self, mock_resume, mock_stream):
        """chat source 返回 SSE 流。"""
        mock_resume.return_value = {
            'approval': self.chat_approval,
            'resume_value': False,
        }
        fake_response = StreamingHttpResponse(
            streaming_content=iter(['data: test\n\n']),
            content_type='text/event-stream',
        )
        mock_stream.return_value = fake_response

        resp = self.client.post(
            f'/api/v1/approvals/{self.chat_approval.interrupt_id}/reject/'
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/event-stream', resp['Content-Type'])
        mock_resume.assert_called_once_with(
            interrupt_id='reject-chat-001',
            approved=False,
        )
        mock_stream.assert_called_once()
        # 拒绝时 approved=False
        call_args = mock_stream.call_args
        self.assertFalse(call_args.kwargs.get('approved', True))

    @unittest.skip('_resume_research 已在 Phase 1 移除，相关测试待后续 Phase 重建')
    @patch('Django_xm.apps.approvals.views.approval_service._resume_research')
    @patch('Django_xm.apps.approvals.views.approval_service.resume_approval')
    def test_reject_deep_research_source_returns_202(self, mock_resume, mock_resume_research):
        """deep_research source 返回 202。"""
        mock_resume.return_value = {
            'approval': self.research_approval,
            'resume_value': False,
        }

        resp = self.client.post(
            f'/api/v1/approvals/{self.research_approval.interrupt_id}/reject/'
        )

        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body['code'], 0)
        self.assertEqual(body['data']['state'], 'processing')
        self.assertEqual(body['data']['interrupt_id'], 'reject-research-001')
        mock_resume_research.assert_called_once_with(
            task_id='research-task-002',
            interrupt_id='reject-research-001',
            resume_value=False,
        )

    @patch('Django_xm.apps.approvals.views.approval_service.resume_approval')
    def test_reject_not_found_returns_error(self, mock_resume):
        """审批不存在时返回错误。"""
        mock_resume.side_effect = ValueError('审批记录不存在: interrupt_id=nonexistent')

        resp = self.client.post('/api/v1/approvals/nonexistent/reject/')

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body['code'], 40002)
        self.assertIn('审批记录不存在', body['message'])
