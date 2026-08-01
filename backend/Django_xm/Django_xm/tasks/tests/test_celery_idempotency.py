"""Celery 任务调度与幂等性单元测试（Task 5.8）。

覆盖 spec `fix-backend-audit-findings` 阶段 1C 变更：
1. SubTask 5.1: analytics.track_event 路由改为 celery 队列（不再 default）
2. SubTask 5.2: CELERY_BEAT_SCHEDULE 增加 cleanup-expired-approvals-every-minute
3. SubTask 5.3: cleanup_expired_approvals 任务配置（soft_time_limit/autoretry_for/路由）
4. SubTask 5.4: TrackedTask._get_or_create_record 使用 get_or_create 原子操作
5. SubTask 5.5: add_documents_to_index_task Redis 锁幂等保护
6. SubTask 5.6: dev.py 不再清空 CELERY_TASK_ROUTES
7. SubTask 5.7: task_failure 信号幂等保护（终态记录不重复标记）

测试策略：
- 配置类测试：直接读 settings，无需 DB
- 行为类测试：mock CeleryTaskRecord.objects 避免 DB 依赖
  （core_celery_task_record 表的迁移使用 SeparateDatabaseAndState，
   测试 DB 中不存在该表，故全部 mock）

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.tasks.test_celery_idempotency --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from django.conf import settings

from Django_xm.apps.core.task_models import CeleryTaskRecord
from Django_xm.tasks.base import TrackedTask
from Django_xm.tasks.rag_tasks import (
    _RAG_ADD_DOCS_LOCK_TTL,
    _acquire_rag_add_docs_lock,
    _release_rag_add_docs_lock,
)

# ============================================================================
# 配置类测试（无需数据库）
# ============================================================================


class CeleryRouteConfigurationTests(unittest.TestCase):
    """SubTask 5.1 + 5.3 + 5.6：路由配置校验。"""

    def test_analytics_track_event_routed_to_celery_queue(self):
        """SubTask 5.1: analytics.track_event 路由为 'celery'（不再 'default'）。"""
        routes = settings.CELERY_TASK_ROUTES
        self.assertIn("analytics.track_event", routes)
        self.assertEqual(
            routes["analytics.track_event"],
            {"queue": "celery"},
            "analytics.track_event 必须路由到 'celery' 队列，避免 default 队列积压",
        )

    def test_approvals_cleanup_route_routed_to_celery_queue(self):
        """SubTask 5.3: approvals.cleanup_expired_approvals 路由到 'celery' 队列。"""
        routes = settings.CELERY_TASK_ROUTES
        self.assertIn("approvals.cleanup_expired_approvals", routes)
        self.assertEqual(
            routes["approvals.cleanup_expired_approvals"],
            {"queue": "celery"},
        )

    def test_dev_settings_preserves_celery_task_routes(self):
        """SubTask 5.6: dev.py 不再清空 CELERY_TASK_ROUTES。

        验证 dev 环境的路由表非空（与 base.py 保持一致），
        避免开发时任务路由正常但生产环境因路由缺失被丢弃的问题。
        """
        import importlib

        import Django_xm.settings.dev as dev_settings

        importlib.reload(dev_settings)
        # dev.py 不应将 CELERY_TASK_ROUTES 重置为空 dict
        import inspect

        source = inspect.getsource(dev_settings)
        self.assertNotIn(
            "CELERY_TASK_ROUTES = {}",
            source,
            "dev.py 不应清空 CELERY_TASK_ROUTES（应保留 base.py 路由表）",
        )


class CeleryBeatScheduleTests(unittest.TestCase):
    """SubTask 5.2：beat 调度包含 cleanup-expired-approvals-every-minute。"""

    def test_beat_schedule_contains_approval_cleanup_entry(self):
        """CELERY_BEAT_SCHEDULE 包含 cleanup-expired-approvals-every-minute 条目。"""
        schedule = settings.CELERY_BEAT_SCHEDULE
        self.assertIn(
            "cleanup-expired-approvals-every-minute",
            schedule,
            "CELERY_BEAT_SCHEDULE 必须包含审批超时清理条目",
        )

    def test_approval_cleanup_schedule_interval_is_60_seconds(self):
        """审批超时清理调度间隔为 60 秒。"""
        entry = settings.CELERY_BEAT_SCHEDULE["cleanup-expired-approvals-every-minute"]
        self.assertEqual(entry["task"], "approvals.cleanup_expired_approvals")
        self.assertEqual(entry["schedule"], 60.0)


class CleanupExpiredApprovalsTaskConfigTests(unittest.TestCase):
    """SubTask 5.3：cleanup_expired_approvals 任务装饰器配置。"""

    def test_task_has_soft_time_limit_120(self):
        """任务配置 soft_time_limit=120，防止大批量审批卡死 worker。"""
        from Django_xm.tasks.approval_tasks import cleanup_expired_approvals

        task = cleanup_expired_approvals
        self.assertEqual(
            task.soft_time_limit,
            120,
            "cleanup_expired_approvals 必须 soft_time_limit=120",
        )

    def test_task_has_autoretry_for_network_errors(self):
        """任务配置 autoretry_for=(ConnectionError, TimeoutError, OSError)。"""
        from Django_xm.tasks.approval_tasks import cleanup_expired_approvals

        task = cleanup_expired_approvals
        autoretry_for = task.autoretry_for
        self.assertIn(ConnectionError, autoretry_for)
        self.assertIn(TimeoutError, autoretry_for)
        self.assertIn(OSError, autoretry_for)

    def test_task_has_max_retries_3(self):
        """任务配置 max_retries=3。"""
        from Django_xm.tasks.approval_tasks import cleanup_expired_approvals

        task = cleanup_expired_approvals
        self.assertEqual(task.max_retries, 3)

    def test_task_has_retry_backoff_enabled(self):
        """任务启用 retry_backoff，避免雪崩。"""
        from Django_xm.tasks.approval_tasks import cleanup_expired_approvals

        task = cleanup_expired_approvals
        self.assertTrue(task.retry_backoff)


# ============================================================================
# TrackedTask 原子性测试（mock DB）
# ============================================================================


class TrackedTaskGetOrCreateTests(unittest.TestCase):
    """SubTask 5.4：TrackedTask._get_or_create_record 使用 get_or_create 原子操作。"""

    def _make_mock_celery_task(self, task_id="test-task-id-001", name="test.task"):
        mock = MagicMock()
        mock.request.id = task_id
        mock.name = name
        mock.request.kwargs = {"param": "value"}
        return mock

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_get_or_create_creates_record_on_first_call(self, mock_objects):
        """首次调用创建任务记录（get_or_create created=True）。"""
        mock_record = MagicMock()
        mock_objects.get_or_create.return_value = (mock_record, True)

        mock_celery_task = self._make_mock_celery_task()
        tracker = TrackedTask(mock_celery_task)
        record = tracker._get_or_create_record()

        self.assertIs(record, mock_record)
        mock_objects.get_or_create.assert_called_once()
        # 验证 get_or_create 的参数
        _args, kwargs = mock_objects.get_or_create.call_args
        self.assertEqual(kwargs["celery_task_id"], "test-task-id-001")
        self.assertIn("defaults", kwargs)
        self.assertEqual(kwargs["defaults"]["task_name"], "test.task")

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_get_or_create_returns_existing_record_on_retry(self, mock_objects):
        """重试场景下复用已存在的记录（关键幂等性测试）。

        模拟 Celery 重试：同一个 task_id 被两次调用 _get_or_create_record，
        第二次应直接返回已存在的记录（created=False），而非抛 IntegrityError。
        """
        mock_record = MagicMock()
        mock_objects.get_or_create.return_value = (mock_record, False)

        mock_celery_task = self._make_mock_celery_task(task_id="test-retry-id-001")

        # 第一次调用：get_or_create 返回 (record, False) 模拟记录已存在
        tracker1 = TrackedTask(mock_celery_task)
        record1 = tracker1._get_or_create_record()
        self.assertIs(record1, mock_record)

        # 第二次调用：模拟重试，应同样返回已存在记录
        tracker2 = TrackedTask(mock_celery_task)
        record2 = tracker2._get_or_create_record()
        self.assertIs(record2, mock_record)

        # 验证 get_or_create 被调用两次（每次创建新 TrackedTask 都会调用）
        self.assertEqual(mock_objects.get_or_create.call_count, 2)

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_get_or_create_does_not_overwrite_existing_record(self, mock_objects):
        """已存在的记录不被 defaults 覆盖。

        get_or_create 的 defaults 仅在新创建时写入，
        已存在记录的 task_name/task_kwargs 不被重试时的 defaults 覆盖。
        """
        # 模拟记录已存在（created=False），返回原始记录
        original_record = MagicMock()
        original_record.task_name = "test.original_name"
        mock_objects.get_or_create.return_value = (original_record, False)

        mock_celery_task = self._make_mock_celery_task(
            task_id="test-no-overwrite-001",
            name="test.changed_name",  # 模拟重试时 name 变了
        )

        tracker = TrackedTask(mock_celery_task)
        record = tracker._get_or_create_record()

        # 验证返回的是已存在记录，task_name 不被 defaults 覆盖
        self.assertEqual(record.task_name, "test.original_name")
        # 验证 defaults 中包含 changed_name（但 get_or_create 不会写入已存在记录）
        _args, kwargs = mock_objects.get_or_create.call_args
        self.assertEqual(kwargs["defaults"]["task_name"], "test.changed_name")

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_get_or_create_caches_record_in_instance(self, mock_objects):
        """_record 缓存：同一 TrackedTask 实例多次调用返回同一对象。"""
        mock_record = MagicMock()
        mock_objects.get_or_create.return_value = (mock_record, True)

        mock_celery_task = self._make_mock_celery_task()
        tracker = TrackedTask(mock_celery_task)
        record1 = tracker._get_or_create_record()
        record2 = tracker._get_or_create_record()
        self.assertIs(record1, record2)
        # 验证只调用了一次 get_or_create（缓存生效）
        mock_objects.get_or_create.assert_called_once()

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_get_or_create_includes_pending_type_in_defaults(self, mock_objects):
        """_pending_type 被正确传入 defaults。"""
        mock_record = MagicMock()
        mock_objects.get_or_create.return_value = (mock_record, True)

        mock_celery_task = self._make_mock_celery_task()
        tracker = TrackedTask(mock_celery_task)
        tracker.set_task_type("rag_index")
        tracker._get_or_create_record()

        _args, kwargs = mock_objects.get_or_create.call_args
        self.assertEqual(
            kwargs["defaults"]["task_type"],
            CeleryTaskRecord.TaskType.RAG_INDEX,
        )

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_get_or_create_handles_invalid_pending_type(self, mock_objects):
        """_pending_type 非法时不写入 defaults（不抛 ValueError）。"""
        mock_record = MagicMock()
        mock_objects.get_or_create.return_value = (mock_record, True)

        mock_celery_task = self._make_mock_celery_task()
        tracker = TrackedTask(mock_celery_task)
        tracker.set_task_type("invalid_type_xyz")
        tracker._get_or_create_record()

        _args, kwargs = mock_objects.get_or_create.call_args
        # 非法 task_type 不应出现在 defaults 中
        self.assertNotIn("task_type", kwargs["defaults"])


# ============================================================================
# task_failure 信号幂等性测试（mock DB）
# ============================================================================


class TaskFailureSignalIdempotencyTests(unittest.TestCase):
    """SubTask 5.7：task_failure 信号幂等保护。"""

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_failure_signal_skips_already_failed_record(self, mock_objects):
        """已处于 FAILURE 终态的记录不再重复标记 mark_failure。"""
        mock_record = MagicMock()
        mock_record.status = CeleryTaskRecord.TaskStatus.FAILURE
        mock_objects.filter.return_value.first.return_value = mock_record

        from Django_xm.tasks.signals import on_task_failure

        sender = MagicMock()
        sender.name = "test.failed_task"

        on_task_failure(
            sender=sender,
            task_id="test-failed-id-001",
            exception=Exception("retry error"),
        )

        # 已处于 FAILURE 终态，不应再调用 mark_failure
        mock_record.mark_failure.assert_not_called()

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_failure_signal_skips_already_revoked_record(self, mock_objects):
        """已处于 REVOKED 终态的记录不再标记 mark_failure。"""
        mock_record = MagicMock()
        mock_record.status = CeleryTaskRecord.TaskStatus.REVOKED
        mock_objects.filter.return_value.first.return_value = mock_record

        from Django_xm.tasks.signals import on_task_failure

        sender = MagicMock()
        sender.name = "test.revoked_task"

        on_task_failure(
            sender=sender,
            task_id="test-revoked-id-001",
            exception=Exception("error after revoke"),
        )

        mock_record.mark_failure.assert_not_called()

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_failure_signal_marks_non_terminal_record(self, mock_objects):
        """非终态记录（STARTED）正常调用 mark_failure。"""
        mock_record = MagicMock()
        mock_record.status = CeleryTaskRecord.TaskStatus.STARTED
        mock_objects.filter.return_value.first.return_value = mock_record

        from Django_xm.tasks.signals import on_task_failure

        sender = MagicMock()
        sender.name = "test.started_task"

        on_task_failure(
            sender=sender,
            task_id="test-started-id-001",
            exception=Exception("runtime error"),
        )

        mock_record.mark_failure.assert_called_once()
        _args, kwargs = mock_record.mark_failure.call_args
        self.assertIn("runtime error", kwargs.get("error_message", ""))

    @patch("Django_xm.apps.core.task_models.CeleryTaskRecord.objects")
    def test_failure_signal_skips_when_record_not_found(self, mock_objects):
        """任务记录不存在时直接 return，不抛异常。"""
        mock_objects.filter.return_value.first.return_value = None

        from Django_xm.tasks.signals import on_task_failure

        sender = MagicMock()
        sender.name = "test.nonexistent_task"

        # 不应抛异常
        on_task_failure(
            sender=sender,
            task_id="nonexistent-task-id-001",
            exception=Exception("error"),
        )


# ============================================================================
# RAG Redis 锁测试（SubTask 5.5）
# ============================================================================


class RagAddDocsLockTests(unittest.TestCase):
    """SubTask 5.5：add_documents_to_index_task Redis 锁幂等保护。"""

    def test_acquire_lock_returns_true_when_redis_available(self):
        """Redis 可用时获锁成功返回 True。"""
        mock_client = MagicMock()
        mock_client.set.return_value = True

        with patch("django_redis.get_redis_connection", return_value=mock_client):
            result = _acquire_rag_add_docs_lock("test-task-id-001")

        self.assertTrue(result)
        # 验证 SET NX EX 调用
        mock_client.set.assert_called_once()
        args, kwargs = mock_client.set.call_args
        self.assertEqual(args[0], "lock:rag_add_docs:test-task-id-001")
        self.assertEqual(args[1], "1")
        self.assertTrue(kwargs.get("nx"))
        self.assertEqual(kwargs.get("ex"), _RAG_ADD_DOCS_LOCK_TTL)

    def test_acquire_lock_returns_false_when_lock_held(self):
        """锁已被持有时返回 False（NX 语义）。"""
        mock_client = MagicMock()
        mock_client.set.return_value = False  # NX 失败

        with patch("django_redis.get_redis_connection", return_value=mock_client):
            result = _acquire_rag_add_docs_lock("test-task-id-002")

        self.assertFalse(result)

    def test_acquire_lock_returns_true_when_no_task_id(self):
        """无 task_id 时放行（由业务层稳定 ID 兜底）。"""
        result = _acquire_rag_add_docs_lock("")
        self.assertTrue(result)

        result_none = _acquire_rag_add_docs_lock(None)
        self.assertTrue(result_none)

    def test_acquire_lock_returns_true_when_redis_unavailable(self):
        """Redis 不可用时放行，不阻塞任务（业务层兜底）。"""
        with patch(
            "django_redis.get_redis_connection",
            side_effect=Exception("Redis connection refused"),
        ):
            result = _acquire_rag_add_docs_lock("test-task-id-003")

        self.assertTrue(result)

    def test_release_lock_deletes_key(self):
        """释放锁时删除 Redis key。"""
        mock_client = MagicMock()

        with patch("django_redis.get_redis_connection", return_value=mock_client):
            _release_rag_add_docs_lock("test-task-id-004")

        mock_client.delete.assert_called_once_with("lock:rag_add_docs:test-task-id-004")

    def test_release_lock_skips_when_no_task_id(self):
        """无 task_id 时跳过释放。"""
        mock_client = MagicMock()

        with patch("django_redis.get_redis_connection", return_value=mock_client):
            _release_rag_add_docs_lock("")

        mock_client.delete.assert_not_called()

    def test_release_lock_does_not_raise_on_redis_error(self):
        """Redis 异常时不抛错（TTL 会自动过期）。"""
        with patch(
            "django_redis.get_redis_connection",
            side_effect=Exception("Redis connection refused"),
        ):
            # 不应抛异常
            _release_rag_add_docs_lock("test-task-id-005")


class RagAddDocsTaskIdempotencyTests(unittest.TestCase):
    """SubTask 5.5：add_documents_to_index_task 在锁被持有时跳过执行。"""

    def test_task_skips_when_lock_held(self):
        """锁被其他 worker 持有时，任务跳过执行并返回 skipped 状态。"""
        from Django_xm.tasks.rag_tasks import add_documents_to_index_task

        # mock 锁被持有 + tracker.mark_success（避免 DB 操作）
        with (
            patch(
                "Django_xm.tasks.rag_tasks._acquire_rag_add_docs_lock",
                return_value=False,
            ),
            patch("Django_xm.tasks.rag_tasks._release_rag_add_docs_lock") as mock_release,
            patch("Django_xm.tasks.base.TrackedTask.mark_success") as mock_mark_success,
        ):
            result = add_documents_to_index_task.apply(
                args=("test-index", ["/tmp/test.pdf"]),  # noqa: S108
                kwargs={"user_id": None, "task_id": None, "original_name": None},
            ).get()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "another_worker_holding_lock")
        # 锁被持有时任务提前 return，不进入 try-finally 块，
        # 因此不应调用 _release_rag_add_docs_lock（未获取的锁不释放）
        mock_release.assert_not_called()
        # 验证 tracker 标记为成功（skipped 也算正常结束）
        mock_mark_success.assert_called_once()

    def test_task_releases_lock_on_failure(self):
        """任务失败时也释放锁（finally 块保障）。"""
        from Django_xm.tasks.rag_tasks import add_documents_to_index_task

        with (
            patch(
                "Django_xm.tasks.rag_tasks._acquire_rag_add_docs_lock",
                return_value=True,
            ),
            patch("Django_xm.tasks.rag_tasks._release_rag_add_docs_lock") as mock_release,
            patch("Django_xm.tasks.rag_tasks.get_index_manager") as mock_manager,
            patch("Django_xm.tasks.base.TrackedTask.mark_started"),
            patch("Django_xm.tasks.base.TrackedTask.mark_failure"),
        ):
            # mock index_manager 返回索引不存在
            manager_mock = MagicMock()
            manager_mock.index_exists.return_value = False
            mock_manager.return_value = manager_mock

            result = add_documents_to_index_task.apply(
                args=("nonexistent-index", ["/tmp/nonexistent.pdf"]),  # noqa: S108
            ).get()

        # 验证返回错误状态
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "索引不存在")
        # 验证锁被释放（finally 块无论成功失败都执行）
        mock_release.assert_called()


if __name__ == "__main__":
    unittest.main()
