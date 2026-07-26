"""approval_tasks 单元测试（Task 14.5）。

覆盖 spec `unify-approval-and-timeout-recovery` 阶段六变更：
1. resume_chat_after_timeout 入口日志验证
2. 缺 chat_session_id 且无 sibling 时记录 ERROR 日志并终态化
3. 缺 chat_session_id 但有 sibling 时从 sibling 恢复 chat_session_id

mock 策略:
- mock Approval.objects.get / ChatSession.objects.filter
- mock _stream_chat_resume_generator 避免真实 LangGraph 调用
- mock complete_approval 验证终态化调用

运行方式:
    cd d:\programming\langchain\langchain_xm\backend\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.tasks.test_approval_tasks --verbosity=2
"""

from __future__ import annotations

import logging
import os
import unittest
from unittest.mock import patch, MagicMock

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django  # noqa: E402
import django.apps  # noqa: E402,F401

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.approvals.models import Approval  # noqa: E402
from Django_xm.tasks.approval_tasks import resume_chat_after_timeout  # noqa: E402


def _make_approval_mock(**overrides):
    """构造 mock Approval 对象（避免数据库依赖）。

    使用 MagicMock 而非真实 ORM 对象，因为本测试关注任务调度逻辑而非数据持久化。
    """
    defaults = {
        'interrupt_id': 'test-interrupt-1',
        'source': Approval.SOURCE_CHAT,
        'source_id': 'test-session-1',
        'chat_session_id': None,  # 默认缺失，用于测试 recovery 逻辑
        'tool_name': 'shell_exec',
        'extra': {'graph_interrupt_id': 'gid-test-1'},
        'save': MagicMock(),
    }
    defaults.update(overrides)
    approval = MagicMock()
    for k, v in defaults.items():
        setattr(approval, k, v)
    return approval


class ResumeChatAfterTimeoutEntryLogTests(unittest.TestCase):
    """验证 resume_chat_after_timeout 入口日志。"""

    @patch('Django_xm.tasks.approval_tasks.Approval.objects.get')
    def test_resume_chat_after_timeout_entry_log(self, mock_get):
        """函数入口记录'[ResumeChatTimeout] 任务被调用'日志。"""
        approval = _make_approval_mock()
        mock_get.return_value = approval

        with self.assertLogs(
            'Django_xm.tasks.approval_tasks', level='INFO'
        ) as cm:
            # 触发函数（会因 chat_session_id 缺失走完整路径）
            with patch(
                'Django_xm.tasks.approval_tasks.Approval.objects.filter'
            ) as mock_filter:
                mock_filter.return_value.exclude.return_value.first.return_value = None
                # mock complete_approval 避免实际终态化
                with patch(
                    'Django_xm.apps.approvals.services.approval_service.complete_approval'
                ):
                    resume_chat_after_timeout.apply(args=['test-interrupt-1']).get()

        # 验证入口日志
        log_text = '\n'.join(cm.output)
        self.assertIn('[ResumeChatTimeout] 任务被调用', log_text)
        self.assertIn('test-interrupt-1', log_text)


class ResumeChatAfterTimeoutMissingSessionTests(unittest.TestCase):
    """缺 chat_session_id 且无法从 sibling 恢复时记录 ERROR 日志并终态化。"""

    @patch('Django_xm.apps.approvals.services.approval_service.complete_approval')
    @patch('Django_xm.tasks.approval_tasks.Approval.objects.get')
    def test_resume_chat_after_timeout_missing_session_id_logs_error(
        self, mock_get, mock_complete
    ):
        """缺 chat_session_id 且无 sibling → 记录 ERROR 日志并调用 complete_approval 终态化。"""
        approval = _make_approval_mock(
            interrupt_id='missing-session-1',
            chat_session_id=None,
            extra={'graph_interrupt_id': 'gid-missing-1'},
        )
        mock_get.return_value = approval

        with patch(
            'Django_xm.tasks.approval_tasks.Approval.objects.filter'
        ) as mock_filter:
            # sibling 查询返回空
            mock_filter.return_value.exclude.return_value.first.return_value = None

            with self.assertLogs(
                'Django_xm.tasks.approval_tasks', level='ERROR'
            ) as cm:
                resume_chat_after_timeout.apply(args=['missing-session-1']).get()

        # 验证 ERROR 日志包含"缺少 chat_session_id"
        log_text = '\n'.join(cm.output)
        self.assertIn('缺少 chat_session_id', log_text)
        self.assertIn('missing-session-1', log_text)

        # 验证调用 complete_approval 终态化为 REJECTED
        mock_complete.assert_called_once()
        call_args = mock_complete.call_args
        self.assertEqual(call_args.args[0], 'missing-session-1')
        self.assertEqual(call_args.args[1], Approval.STATE_REJECTED)

    @patch('Django_xm.apps.approvals.services.approval_service.complete_approval')
    @patch('Django_xm.tasks.approval_tasks.Approval.objects.get')
    def test_resume_chat_after_timeout_missing_session_id_no_graph_interrupt(
        self, mock_get, mock_complete
    ):
        """缺 chat_session_id 且 extra 无 graph_interrupt_id → 直接终态化。"""
        approval = _make_approval_mock(
            interrupt_id='missing-session-2',
            chat_session_id=None,
            extra={},  # 无 graph_interrupt_id
        )
        mock_get.return_value = approval

        with patch(
            'Django_xm.tasks.approval_tasks.Approval.objects.filter'
        ) as mock_filter:
            mock_filter.return_value.exclude.return_value.first.return_value = None

            with self.assertLogs(
                'Django_xm.tasks.approval_tasks', level='ERROR'
            ) as cm:
                resume_chat_after_timeout.apply(args=['missing-session-2']).get()

        log_text = '\n'.join(cm.output)
        self.assertIn('缺少 chat_session_id', log_text)
        mock_complete.assert_called_once()


class ResumeChatAfterTimeoutRecoverFromSiblingTests(unittest.TestCase):
    """缺 chat_session_id 但有 sibling 时从 sibling 恢复。"""

    @patch('Django_xm.apps.chat.models.ChatSession.objects.filter')
    @patch('Django_xm.tasks.approval_tasks.Approval.objects.get')
    def test_resume_chat_after_timeout_recovers_session_from_sibling(
        self, mock_get, mock_session_filter
    ):
        """缺 chat_session_id 但 sibling 有 → 从 sibling 恢复 chat_session_id。"""
        approval = _make_approval_mock(
            interrupt_id='recover-1',
            chat_session_id=None,
            extra={'graph_interrupt_id': 'gid-recover-1'},
        )
        mock_get.return_value = approval

        # sibling mock：有 chat_session_id
        sibling = MagicMock()
        sibling.chat_session_id = 'session-from-sibling'

        with patch(
            'Django_xm.tasks.approval_tasks.Approval.objects.filter'
        ) as mock_filter:
            mock_filter.return_value.exclude.return_value.first.return_value = sibling

            # mock ChatSession 查询返回 session（避免后续 ChatSession.objects.filter 报错）
            mock_session = MagicMock()
            mock_session.user_id = 42
            mock_session_filter.return_value.first.return_value = mock_session

            # mock _stream_chat_resume_generator 避免真实 LangGraph 调用
            async def _fake_gen(*args, **kwargs):
                yield 'event-1'
                yield 'event-2'

            with patch(
                'Django_xm.apps.chat.views_chat._stream_chat_resume_generator',
                side_effect=_fake_gen,
            ):
                with self.assertLogs(
                    'Django_xm.tasks.approval_tasks', level='INFO'
                ) as cm:
                    resume_chat_after_timeout.apply(args=['recover-1']).get()

        # 验证从 sibling 恢复 chat_session_id
        self.assertEqual(approval.chat_session_id, 'session-from-sibling')
        # 验证 approval.save 被调用（持久化恢复的 chat_session_id）
        approval.save.assert_called_once_with(update_fields=['chat_session_id'])

        # 验证日志包含恢复信息
        log_text = '\n'.join(cm.output)
        self.assertIn('chat_session_id 从 sibling 恢复', log_text)
        self.assertIn('recover-1', log_text)
        self.assertIn('session-from-sibling', log_text)


class ResumeChatAfterTimeoutNonChatSourceTests(unittest.TestCase):
    """非 chat 来源审批超时跳过恢复。"""

    @patch('Django_xm.tasks.approval_tasks.Approval.objects.get')
    def test_resume_chat_after_timeout_skips_non_chat_source(self, mock_get):
        """source != chat → 跳过恢复，记录 INFO 日志。"""
        approval = _make_approval_mock(
            interrupt_id='non-chat-1',
            source=Approval.SOURCE_DEEP_RESEARCH,
            chat_session_id='some-session',
        )
        mock_get.return_value = approval

        with self.assertLogs(
            'Django_xm.tasks.approval_tasks', level='INFO'
        ) as cm:
            resume_chat_after_timeout.apply(args=['non-chat-1']).get()

        log_text = '\n'.join(cm.output)
        self.assertIn('非 chat 来源', log_text)


if __name__ == "__main__":
    unittest.main()