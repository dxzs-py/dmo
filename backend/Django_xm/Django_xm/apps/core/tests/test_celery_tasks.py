"""
Celery 任务单元测试
使用 CELERY_TASK_ALWAYS_EAGER 同步执行，无需启动 Worker
"""
from django.test import TestCase, override_settings
from unittest.mock import patch, MagicMock

from Django_xm.tasks.base import TrackedTask, cleanup_old_task_records, check_stale_tasks
from Django_xm.apps.core.task_models import CeleryTaskRecord


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class TrackedTaskTestCase(TestCase):

    def test_tracked_task_pending_type_and_user(self):
        mock_task = MagicMock()
        mock_task.request.id = 'test-task-id-001'
        mock_task.request.kwargs = {'index_name': 'test'}
        mock_task.name = 'rag.create_index'

        tracker = TrackedTask(mock_task)
        tracker._pending_type = 'rag_index'
        tracker._pending_user_id = None

        record = tracker._get_or_create_record()
        self.assertEqual(record.celery_task_id, 'test-task-id-001')
        self.assertEqual(record.task_name, 'rag.create_index')
        self.assertEqual(record.task_type, CeleryTaskRecord.TaskType.RAG_INDEX)

    def test_tracked_task_mark_started(self):
        mock_task = MagicMock()
        mock_task.request.id = 'test-task-id-002'
        mock_task.request.kwargs = {}
        mock_task.name = 'base.debug_task'

        tracker = TrackedTask(mock_task)
        tracker.mark_started()

        record = CeleryTaskRecord.objects.get(celery_task_id='test-task-id-002')
        self.assertEqual(record.status, CeleryTaskRecord.TaskStatus.STARTED)
        self.assertIsNotNone(record.started_at)

    def test_tracked_task_mark_success(self):
        mock_task = MagicMock()
        mock_task.request.id = 'test-task-id-003'
        mock_task.request.kwargs = {}
        mock_task.name = 'base.debug_task'

        tracker = TrackedTask(mock_task)
        tracker.mark_started()
        tracker.mark_success(result={'chunk_count': 10})

        record = CeleryTaskRecord.objects.get(celery_task_id='test-task-id-003')
        self.assertEqual(record.status, CeleryTaskRecord.TaskStatus.SUCCESS)
        self.assertEqual(record.progress, 100)
        self.assertIsNotNone(record.completed_at)
        self.assertIsNotNone(record.runtime_seconds)

    def test_tracked_task_mark_failure(self):
        mock_task = MagicMock()
        mock_task.request.id = 'test-task-id-004'
        mock_task.request.kwargs = {}
        mock_task.name = 'base.debug_task'

        tracker = TrackedTask(mock_task)
        tracker.mark_started()
        tracker.mark_failure(error_message='测试失败')

        record = CeleryTaskRecord.objects.get(celery_task_id='test-task-id-004')
        self.assertEqual(record.status, CeleryTaskRecord.TaskStatus.FAILURE)
        self.assertEqual(record.error_message, '测试失败')

    def test_tracked_task_update_progress(self):
        mock_task = MagicMock()
        mock_task.request.id = 'test-task-id-005'
        mock_task.request.kwargs = {}
        mock_task.name = 'base.debug_task'

        tracker = TrackedTask(mock_task)
        tracker.mark_started()
        tracker.update_progress(50, '处理中')

        record = CeleryTaskRecord.objects.get(celery_task_id='test-task-id-005')
        self.assertEqual(record.status, CeleryTaskRecord.TaskStatus.PROGRESS)
        self.assertEqual(record.progress, 50)
        self.assertEqual(record.progress_message, '处理中')

    def test_tracked_task_sync_to_task_manager(self):
        mock_task = MagicMock()
        mock_task.request.id = 'test-task-id-006'
        mock_task.request.kwargs = {}
        mock_task.name = 'rag.create_index'

        tracker = TrackedTask(mock_task)
        tracker._task_manager_id = 'tm-001'

        with patch('Django_xm.tasks.base.update_task_status') as mock_update:
            tracker.mark_started()
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            self.assertEqual(call_args[0][0], 'tm-001')
            self.assertEqual(call_args[0][1]['status'], 'running')


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class CeleryTaskRecordTestCase(TestCase):

    def test_task_type_choices_include_chat(self):
        types = dict(CeleryTaskRecord.TaskType.choices)
        self.assertIn('chat_cleanup', types)
        self.assertIn('chat_index', types)
        self.assertIn('chat_storage', types)

    def test_mark_started_sets_status(self):
        record = CeleryTaskRecord.objects.create(
            celery_task_id='rec-001',
            task_name='test.task',
        )
        record.mark_started(worker_name='worker1')
        record.refresh_from_db()
        self.assertEqual(record.status, CeleryTaskRecord.TaskStatus.STARTED)
        self.assertEqual(record.worker_name, 'worker1')

    def test_mark_success_calculates_runtime(self):
        record = CeleryTaskRecord.objects.create(
            celery_task_id='rec-002',
            task_name='test.task',
        )
        record.mark_started()
        record.mark_success(result={'ok': True})
        record.refresh_from_db()
        self.assertEqual(record.status, CeleryTaskRecord.TaskStatus.SUCCESS)
        self.assertIsNotNone(record.runtime_seconds)
        self.assertGreaterEqual(record.runtime_seconds, 0)

    def test_mark_failure_sets_error(self):
        record = CeleryTaskRecord.objects.create(
            celery_task_id='rec-003',
            task_name='test.task',
        )
        record.mark_started()
        record.mark_failure(error_message='出错了')
        record.refresh_from_db()
        self.assertEqual(record.status, CeleryTaskRecord.TaskStatus.FAILURE)
        self.assertEqual(record.error_message, '出错了')


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class CleanupTaskTestCase(TestCase):

    def test_cleanup_old_task_records(self):
        from django.utils import timezone
        from datetime import timedelta

        old_record = CeleryTaskRecord.objects.create(
            celery_task_id='old-001',
            task_name='test.old',
            status=CeleryTaskRecord.TaskStatus.SUCCESS,
        )
        old_record.created_at = timezone.now() - timedelta(days=60)
        old_record.save()

        recent_record = CeleryTaskRecord.objects.create(
            celery_task_id='recent-001',
            task_name='test.recent',
            status=CeleryTaskRecord.TaskStatus.SUCCESS,
        )

        result = cleanup_old_task_records(days=30)

        self.assertTrue(CeleryTaskRecord.objects.filter(celery_task_id='recent-001').exists())
        self.assertFalse(CeleryTaskRecord.objects.filter(celery_task_id='old-001').exists())

    def test_check_stale_tasks(self):
        from django.utils import timezone
        from datetime import timedelta

        stale_record = CeleryTaskRecord.objects.create(
            celery_task_id='stale-001',
            task_name='test.stale',
            status=CeleryTaskRecord.TaskStatus.STARTED,
        )
        stale_record.started_at = timezone.now() - timedelta(minutes=120)
        stale_record.save()

        active_record = CeleryTaskRecord.objects.create(
            celery_task_id='active-001',
            task_name='test.active',
            status=CeleryTaskRecord.TaskStatus.STARTED,
        )
        active_record.started_at = timezone.now()
        active_record.save()

        result = check_stale_tasks(timeout_minutes=60)

        stale_record.refresh_from_db()
        active_record.refresh_from_db()
        self.assertEqual(stale_record.status, CeleryTaskRecord.TaskStatus.FAILURE)
        self.assertEqual(active_record.status, CeleryTaskRecord.TaskStatus.STARTED)
