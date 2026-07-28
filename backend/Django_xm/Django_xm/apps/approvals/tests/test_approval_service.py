"""approval_service 单元测试。

覆盖 request_approval / request_approval_async / resume_approval /
complete_approval / timeout_approval / _resume_research / 历史别名。
"""

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service

# 测试用审批数据
APPROVAL_DATA = {
    'tool_name': 'shell_exec',
    'title': '确认执行',
    'description': '执行 shell 命令',
    'action': Approval.ACTION_CONFIRM,
    'operation': 'rm -rf /tmp/test',
    'danger_level': 'high',
    'parameters': {'command': 'rm -rf /tmp/test'},
    'session_id': 'test-chat-session-id',
    'extra': {},
}


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


class RequestApprovalTests(TestCase):
    """测试 request_approval（同步发起审批）。"""

    def setUp(self):
        # async 测试可能影响 TestCase 事务回滚，需手动清理确保测试隔离
        Approval.objects.all().delete()

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_pending')
    def test_creates_approval_record(self, mock_persist, mock_publish):
        """调用 request_approval 后数据库新增一条 pending 审批记录。"""
        before = Approval.objects.count()
        approval_service.request_approval(
            source=Approval.SOURCE_CHAT,
            source_id='test-source-id',
            interrupt_id='test-interrupt-id',
            approval_data=APPROVAL_DATA,
        )
        after = Approval.objects.count()
        self.assertEqual(after, before + 1)

        approval = Approval.objects.get(interrupt_id='test-interrupt-id')
        self.assertEqual(approval.source, Approval.SOURCE_CHAT)
        self.assertEqual(approval.source_id, 'test-source-id')
        self.assertEqual(approval.state, Approval.STATE_PENDING)
        self.assertEqual(approval.tool_name, 'shell_exec')

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_pending')
    def test_persists_to_redis(self, mock_persist, mock_publish):
        """request_approval 调用 persist_approval_pending 持久化 pending 数据到 Redis。"""
        approval_service.request_approval(
            source=Approval.SOURCE_CHAT,
            source_id='test-source-id',
            interrupt_id='test-interrupt-id',
            approval_data=APPROVAL_DATA,
        )
        mock_persist.assert_called_once()
        args, kwargs = mock_persist.call_args
        # 位置参数: (source_id, pending_data)
        self.assertEqual(args[0], 'test-source-id')
        self.assertEqual(args[1]['interrupt_id'], 'test-interrupt-id')
        self.assertEqual(args[1]['state'], Approval.STATE_PENDING)

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_pending')
    def test_broadcasts_pending(self, mock_persist, mock_publish):
        """request_approval 通过 _broadcast_approval_changed 广播 pending 状态。"""
        approval_service.request_approval(
            source=Approval.SOURCE_CHAT,
            source_id='test-source-id',
            interrupt_id='test-interrupt-id',
            approval_data=APPROVAL_DATA,
        )
        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        # _broadcast_approval_changed(approval, state)
        self.assertEqual(args[1], Approval.STATE_PENDING)
        self.assertEqual(args[0].source_id, 'test-source-id')


class RequestApprovalAsyncTests(TransactionTestCase):
    """测试 request_approval_async（异步发起审批）。

    使用 TransactionTestCase 而非 TestCase，因为 TestCase 的事务包装与
    async 数据库操作冲突，会导致后续测试连接失效或卡死。
    TransactionTestCase 通过 truncate 而非 rollback 清理数据，与 async 兼容。

    重写 _fixture_teardown 显式传入 allow_cascade=True，使用 TRUNCATE ... CASCADE
    避免 core_celery_task_record → langchain_users 外键约束错误。
    （Django 默认 allow_cascade 由 available_apps 推导，类属性 allow_cascade 无效）
    """

    def _fixture_teardown(self):
        for db_name in self._databases_names(include_mirrors=False):
            call_command(
                "flush",
                verbosity=0,
                interactive=False,
                database=db_name,
                reset_sequences=False,
                allow_cascade=True,
                inhibit_post_migrate=True,
            )

    def setUp(self):
        # 异步函数内部直接调用同步 ORM，需要允许 async unsafe
        os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = '1'
        self.addCleanup(os.environ.pop, 'DJANGO_ALLOW_ASYNC_UNSAFE', None)
        Approval.objects.all().delete()

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed_async', new_callable=AsyncMock)
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_pending')
    async def test_async_creates_approval_record(self, mock_persist, mock_broadcast_sync, mock_broadcast_async):
        """异步发起审批同样写入数据库 pending 记录。"""
        before = Approval.objects.count()
        await approval_service.request_approval_async(
            source=Approval.SOURCE_CHAT,
            source_id='test-source-id',
            interrupt_id='test-interrupt-id',
            approval_data=APPROVAL_DATA,
        )
        after = Approval.objects.count()
        self.assertEqual(after, before + 1)
        approval = Approval.objects.get(interrupt_id='test-interrupt-id')
        self.assertEqual(approval.state, Approval.STATE_PENDING)

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed_async', new_callable=AsyncMock)
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_pending')
    async def test_async_calls_broadcast_async_not_sync(self, mock_persist, mock_broadcast_sync, mock_broadcast_async):
        """异步版本调用 _broadcast_approval_changed_async，而非同步 _broadcast_approval_changed。"""
        await approval_service.request_approval_async(
            source=Approval.SOURCE_CHAT,
            source_id='test-source-id',
            interrupt_id='test-interrupt-id',
            approval_data=APPROVAL_DATA,
        )
        mock_broadcast_async.assert_called_once()
        mock_broadcast_sync.assert_not_called()


class ResumeApprovalTests(TestCase):
    """测试 resume_approval（恢复审批）。"""

    def setUp(self):
        Approval.objects.all().delete()

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_acquires_distributed_lock(self, mock_get_redis, mock_publish):
        """resume_approval 通过 Redis SET NX 获取分布式锁。"""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True  # 锁获取成功
        mock_get_redis.return_value = mock_redis

        _make_approval(interrupt_id='test-interrupt-id')

        approval_service.resume_approval(
            interrupt_id='test-interrupt-id',
            approved=True,
        )

        mock_redis.set.assert_called_once()
        args, kwargs = mock_redis.set.call_args
        self.assertEqual(args[0], 'approval:lock:test-interrupt-id')
        self.assertTrue(kwargs.get('nx'))
        self.assertEqual(kwargs.get('ex'), approval_service.APPROVAL_LOCK_TTL)

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_broadcasts_processing(self, mock_get_redis, mock_publish):
        """resume_approval 立即广播 processing 状态。"""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_get_redis.return_value = mock_redis

        _make_approval(interrupt_id='test-interrupt-id')

        approval_service.resume_approval(
            interrupt_id='test-interrupt-id',
            approved=True,
        )

        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        # _broadcast_approval_changed(approval, state)
        self.assertEqual(args[1], Approval.STATE_PROCESSING)

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_returns_idempotent_when_state_not_pending(self, mock_get_redis, mock_publish):
        """审批状态非 pending 时幂等返回（已终态/处理中）。"""
        _make_approval(
            interrupt_id='test-interrupt-id',
            state=Approval.STATE_APPROVED,
        )

        result = approval_service.resume_approval(
            interrupt_id='test-interrupt-id',
            approved=True,
        )

        # 状态检查在获取锁之前，锁不应被获取
        mock_get_redis.assert_not_called()
        # 幂等返回，不抛异常
        self.assertTrue(result.get('idempotent'))
        self.assertIsNone(result.get('resume_value'))
        self.assertIsNone(result.get('stream_generator'))

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_returns_idempotent_when_lock_already_held(self, mock_get_redis, mock_publish):
        """锁已被占用时幂等返回（并发恢复请求）。"""
        mock_redis = MagicMock()
        mock_redis.set.return_value = None  # NX 失败，锁已被占用
        mock_get_redis.return_value = mock_redis

        _make_approval(interrupt_id='test-interrupt-id')

        result = approval_service.resume_approval(
            interrupt_id='test-interrupt-id',
            approved=True,
        )

        mock_redis.set.assert_called_once()
        # 锁获取失败，不应进入 try 块，不广播 processing
        mock_publish.assert_not_called()
        # 幂等返回，不抛异常
        self.assertTrue(result.get('idempotent'))
        self.assertIsNone(result.get('resume_value'))


class CompleteApprovalTests(TestCase):
    """测试 complete_approval（完成审批）。"""

    def setUp(self):
        Approval.objects.all().delete()

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_updates_state_to_approved(self, mock_get_redis, mock_persist, mock_publish):
        """complete_approval 将状态更新为 approved。"""
        approval = _make_approval(interrupt_id='test-interrupt-id')
        approval_service.complete_approval(
            interrupt_id='test-interrupt-id',
            state=Approval.STATE_APPROVED,
        )
        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_APPROVED)
        self.assertIsNotNone(approval.resolved_at)

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_updates_state_to_rejected(self, mock_get_redis, mock_persist, mock_publish):
        """complete_approval 将状态更新为 rejected。"""
        approval = _make_approval(interrupt_id='test-interrupt-id')
        approval_service.complete_approval(
            interrupt_id='test-interrupt-id',
            state=Approval.STATE_REJECTED,
        )
        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_REJECTED)

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_persists_processed_to_redis(self, mock_get_redis, mock_persist, mock_publish):
        """complete_approval 调用 persist_approval_processed 持久化最终状态。"""
        _make_approval(interrupt_id='test-interrupt-id')
        approval_service.complete_approval(
            interrupt_id='test-interrupt-id',
            state=Approval.STATE_APPROVED,
        )
        mock_persist.assert_called_once()
        args, kwargs = mock_persist.call_args
        self.assertEqual(args[0], 'test-interrupt-id')
        self.assertEqual(args[1]['state'], Approval.STATE_APPROVED)

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_broadcasts_approved(self, mock_get_redis, mock_persist, mock_publish):
        """complete_approval 广播 approved 状态。"""
        _make_approval(interrupt_id='test-interrupt-id')
        approval_service.complete_approval(
            interrupt_id='test-interrupt-id',
            state=Approval.STATE_APPROVED,
        )
        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        # _broadcast_approval_changed(approval, state)
        self.assertEqual(args[1], Approval.STATE_APPROVED)

    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_releases_lock(self, mock_get_redis, mock_persist, mock_publish):
        """complete_approval 释放分布式锁。"""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        _make_approval(interrupt_id='test-interrupt-id')
        approval_service.complete_approval(
            interrupt_id='test-interrupt-id',
            state=Approval.STATE_APPROVED,
        )
        mock_redis.delete.assert_called_once_with('approval:lock:test-interrupt-id')


class TimeoutApprovalTests(TestCase):
    """测试 timeout_approval（审批超时处理）。"""

    def setUp(self):
        Approval.objects.all().delete()

    @patch('Django_xm.tasks.approval_tasks.resume_chat_after_timeout')
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    def test_sets_state_to_processing_for_resume(self, mock_get_redis, mock_persist, mock_publish, mock_resume_task):
        """timeout_approval 将 pending 审批置为 processing 以触发恢复流程。

        设计意图（见 timeout_approval docstring）：
        pending→processing(resume_value=TIMEOUT_DECISION)，然后走批量恢复逻辑。
        超时不是终态，而是触发恢复的信号；最终终态由 complete_approval 设置。
        """
        approval = _make_approval(
            interrupt_id='test-interrupt-id',
            source=Approval.SOURCE_CHAT,
        )
        approval_service.timeout_approval('test-interrupt-id')
        approval.refresh_from_db()
        # 超时后状态为 processing（设置 TIMEOUT_DECISION），等待恢复流程完成
        self.assertEqual(approval.state, Approval.STATE_PROCESSING)
        # chat 场景应派发 Celery 恢复任务
        mock_resume_task.delay.assert_called_once_with('test-interrupt-id')

    @patch('Django_xm.apps.approvals.services.approval_service._resume_research')
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    @unittest.skip('_resume_research 已在 Phase 1 移除，相关测试待后续 Phase 重建')
    def test_triggers_resume_research_for_deep_research(self, mock_get_redis, mock_persist, mock_publish, mock_resume):
        """深度研究审批超时触发 _resume_research（resume_value=False）。"""
        _make_approval(
            interrupt_id='test-interrupt-id',
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id='test-task-id',
        )
        approval_service.timeout_approval('test-interrupt-id')
        mock_resume.assert_called_once()
        args, kwargs = mock_resume.call_args
        self.assertEqual(kwargs['task_id'], 'test-task-id')
        self.assertEqual(kwargs['interrupt_id'], 'test-interrupt-id')
        self.assertEqual(kwargs['resume_value'], False)

    @patch('Django_xm.apps.approvals.services.approval_service._resume_research')
    @patch('Django_xm.apps.approvals.services.approval_service._broadcast_approval_changed')
    @patch('Django_xm.apps.approvals.services.approval_service.persist_approval_processed')
    @patch('Django_xm.apps.approvals.services.approval_service._get_redis_client')
    @unittest.skip('_resume_research 已在 Phase 1 移除，相关测试待后续 Phase 重建')
    def test_skips_already_processed(self, mock_get_redis, mock_persist, mock_publish, mock_resume):
        """已处理的审批超时调用跳过，不触发任何副作用。"""
        approval = _make_approval(
            interrupt_id='test-interrupt-id',
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id='test-task-id',
            state=Approval.STATE_APPROVED,
        )
        approval_service.timeout_approval('test-interrupt-id')
        approval.refresh_from_db()
        # 状态不变
        self.assertEqual(approval.state, Approval.STATE_APPROVED)
        # 不触发恢复
        mock_resume.assert_not_called()
        # 不广播
        mock_publish.assert_not_called()


@unittest.skip('_resume_research 已在 Phase 1 移除，相关测试待后续 Phase 重建')
class ResumeResearchTests(TestCase):
    """测试 _resume_research（触发深度研究恢复 Celery 任务）。"""

    @patch('Django_xm.tasks.research_resume_task.resume_research_task')
    def test_triggers_celery_task(self, mock_task):
        """_resume_research 调用 resume_research_task.delay。"""
        approval_service._resume_research(
            task_id='test-task-id',
            interrupt_id='test-interrupt-id',
            resume_value=True,
        )
        mock_task.delay.assert_called_once()
        args, kwargs = mock_task.delay.call_args
        self.assertEqual(kwargs['task_id'], 'test-task-id')
        self.assertEqual(kwargs['interrupt_id'], 'test-interrupt-id')
        self.assertEqual(kwargs['resume_value'], True)

    @patch('Django_xm.tasks.research_resume_task.resume_research_task')
    def test_raises_on_exception(self, mock_task):
        """Celery task 调用异常时 _resume_research 抛出。"""
        mock_task.delay.side_effect = RuntimeError('celery down')
        with self.assertRaises(RuntimeError):
            approval_service._resume_research(
                task_id='test-task-id',
                interrupt_id='test-interrupt-id',
                resume_value=True,
            )


class ApprovalHistoryAliasTests(TestCase):
    """测试 get_approval_history / get_approval_history_by_source 别名。"""

    def test_aliases_are_same(self):
        """get_approval_history 与 get_approval_history_by_source 引用同一函数。"""
        self.assertIs(
            approval_service.get_approval_history,
            approval_service.get_approval_history_by_source,
        )

    @patch('Django_xm.apps.approvals.services.approval_service._get_approval_history_from_store')
    def test_both_return_same_result(self, mock_store_history):
        """两个别名调用底层 store 返回相同结果。"""
        mock_store_history.return_value = [{'interrupt_id': 'x', 'state': 'pending'}]
        r1 = approval_service.get_approval_history('test-source-id')
        r2 = approval_service.get_approval_history_by_source('test-source-id')
        self.assertEqual(r1, r2)
        self.assertEqual(mock_store_history.call_count, 2)


# ====================================================================
# Task 14.4 补充：ApprovalLifecycleService.complete_batch / complete_approval sibling cleanup
# 原实现 approval_service._finalize_waiting_siblings_sync 已重构为
# ApprovalLifecycleService.complete_batch（三模块共享入口，根因 C 修复）。
# 详见 test_approval_service_sibling_cleanup.py（独立文件）。
# 以下用例为 spec 验证要求保留在 test_approval_service.py 中。
# ====================================================================


class CompleteBatchTask14Tests(TestCase):
    """Task 14.4: ApprovalLifecycleService.complete_batch 在 sibling 终态为 timeout 时发布 TOOL_CALL_TIMEOUT 事件。

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
        """sibling 终态为 timeout 时调用 _publish_tool_call_timeout_event。"""
        from Django_xm.common.approval_lifecycle import service as approval_lifecycle_service

        _make_approval(
            interrupt_id='trigger-task14',
            state=Approval.STATE_TIMEOUT,
            extra={'graph_interrupt_id': 'gid-task14-1'},
        )
        # sibling: extra 无 _approved 字段，回退到 trigger.state = timeout
        _make_approval(
            interrupt_id='sibling-task14',
            state=Approval.STATE_WAITING,
            extra={'graph_interrupt_id': 'gid-task14-1'},
        )

        result = approval_lifecycle_service.complete_batch(
            'gid-task14-1', 'trigger-task14', Approval.STATE_TIMEOUT,
        )

        # trigger 已终态 → skipped；sibling waiting → success
        self.assertEqual(result['success'], 1)
        self.assertEqual(result['skipped'], 1)
        mock_publish_timeout.assert_called_once()
        call_args = mock_publish_timeout.call_args
        self.assertEqual(call_args.args[0].interrupt_id, 'sibling-task14')

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

        集成测试：不 mock complete_batch，验证完整端到端行为。
        trigger 终态为 approved，sibling _approved=True → 终态 approved。
        """
        approval = _make_approval(
            interrupt_id='complete-task14',
            state=Approval.STATE_PROCESSING,
            extra={
                'graph_interrupt_id': 'gid-task14-2',
                '_resume_value': True,
                '_approved': True,
            },
        )
        sibling = _make_approval(
            interrupt_id='sibling-task14-2',
            state=Approval.STATE_WAITING,
            extra={'graph_interrupt_id': 'gid-task14-2', '_approved': True},
        )
        approval_service.complete_approval(
            interrupt_id='complete-task14',
            state=Approval.STATE_APPROVED,
        )
        # 验证 trigger 已终态化
        approval.refresh_from_db()
        self.assertEqual(approval.state, Approval.STATE_APPROVED)
        self.assertIsNotNone(approval.resolved_at)
        # 验证 sibling 也被终态化（approved）
        sibling.refresh_from_db()
        self.assertEqual(sibling.state, Approval.STATE_APPROVED)
