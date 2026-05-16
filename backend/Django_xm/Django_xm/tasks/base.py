"""
基础 Celery 任务
存放通用、调试类的任务，以及任务追踪基类

全局默认配置（settings）:
    CELERY_TASK_ACKS_LATE = True
    CELERY_TASK_REJECT_ON_WORKER_LOST = True
    无需在每个 @shared_task 中重复声明
"""
import logging
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


class TrackedTask:
    """
    可追踪的 Celery 任务混入类

    在任务执行过程中自动同步状态到数据库和 Redis 缓存，
    实现任务进度追踪和历史查询

    用法:
        @shared_task(bind=True)
        def my_task(self, **kwargs):
            tracker = TrackedTask(self)
            tracker.set_task_type('rag_index')
            tracker.set_created_by(user_id)
            tracker.set_task_manager_id(task_id)
            tracker.mark_started()
            tracker.update_progress(50, "处理中...")
            ...
            tracker.mark_success(result={"key": "value"})
    """

    def __init__(self, celery_task):
        self.celery_task = celery_task
        self._record = None
        self._task_manager_id = None
        self._pending_type = None
        self._pending_user_id = None
        self._sync_fn = None

    def _get_or_create_record(self):
        if self._record is not None:
            return self._record

        from Django_xm.apps.core.task_models import CeleryTaskRecord

        task_id = self.celery_task.request.id
        task_name = self.celery_task.name

        try:
            self._record = CeleryTaskRecord.objects.get(celery_task_id=task_id)
        except CeleryTaskRecord.DoesNotExist:
            create_kwargs = {
                'celery_task_id': task_id,
                'task_name': task_name,
                'task_kwargs': self.celery_task.request.kwargs or {},
            }
            if self._pending_type:
                try:
                    create_kwargs['task_type'] = CeleryTaskRecord.TaskType(self._pending_type)
                except ValueError:
                    pass
            if self._pending_user_id:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                try:
                    create_kwargs['created_by'] = User.objects.get(id=self._pending_user_id)
                except User.DoesNotExist:
                    pass

            self._record = CeleryTaskRecord.objects.create(**create_kwargs)

        return self._record

    def _sync_to_task_manager(self, status_updates: dict):
        if not self._task_manager_id:
            return
        try:
            if self._sync_fn:
                self._sync_fn(self._task_manager_id, status_updates)
        except Exception as e:
            logger.debug(f"[TrackedTask] sync_fn 同步失败: {e}")

        try:
            from Django_xm.apps.common.task_manager import (
                update_task_status as common_update,
                create_task as common_create,
                TaskType,
            )
            result = common_update(self._task_manager_id, status_updates)
            if result is None:
                common_create(
                    task_type=TaskType.OTHER,
                    task_id=self._task_manager_id,
                    user_id=self._pending_user_id,
                    task_name=self.celery_task.name,
                )
                common_update(self._task_manager_id, status_updates)
        except Exception as e:
            logger.debug(f"[TrackedTask] 同步 TaskManager 失败: {e}")

    def set_task_manager_id(self, task_id: str, sync_fn=None):
        self._task_manager_id = task_id
        if sync_fn:
            self._sync_fn = sync_fn

    def mark_started(self, worker_name: str = ''):
        record = self._get_or_create_record()
        record.mark_started(worker_name=worker_name)
        self._sync_to_task_manager({
            'status': 'started',
            'start_time': record.started_at.isoformat() if record.started_at else None,
        })

    def update_progress(self, progress: int, message: str = ''):
        record = self._get_or_create_record()
        record.mark_progress(progress, message)
        self._sync_to_task_manager({
            'status': 'progress',
            'progress': progress,
            'current_step': message,
        })

    def mark_success(self, result=None):
        record = self._get_or_create_record()
        record.mark_success(result=result)
        self._sync_to_task_manager({
            'status': 'success',
            'progress': 100,
            'end_time': record.completed_at.isoformat() if record.completed_at else None,
            'result': result,
        })

    def mark_failure(self, error_message: str = ''):
        record = self._get_or_create_record()
        record.mark_failure(error_message=error_message)
        self._sync_to_task_manager({
            'status': 'failure',
            'error': error_message,
            'end_time': record.completed_at.isoformat() if record.completed_at else None,
        })

    def set_task_type(self, task_type: str):
        self._pending_type = task_type
        try:
            record = self._get_or_create_record()
            from Django_xm.apps.core.task_models import CeleryTaskRecord
            task_type_enum = CeleryTaskRecord.TaskType(task_type)
            record.task_type = task_type_enum
            record.save(update_fields=['task_type', 'updated_at'])
        except Exception:
            pass

    def set_created_by(self, user_id: int):
        self._pending_user_id = user_id
        try:
            record = self._get_or_create_record()
            from django.contrib.auth import get_user_model
            User = get_user_model()
            record.created_by = User.objects.get(id=user_id)
            record.save(update_fields=['created_by', 'updated_at'])
        except Exception:
            pass


@shared_task(
    bind=True,
    name='base.debug_task',
    soft_time_limit=60,
)
def debug_task(self):
    logger.info(f"[Celery] 调试任务执行，请求：{self.request!r}")
    return {'status': 'success', 'request': repr(self.request)}


@shared_task(
    bind=True,
    name='base.cleanup_old_task_records',
    max_retries=1,
    soft_time_limit=300,
)
def cleanup_old_task_records(self, days: int = 30):
    from django.utils import timezone
    from datetime import timedelta
    from Django_xm.apps.core.task_models import CeleryTaskRecord

    cutoff = timezone.now() - timedelta(days=days)

    try:
        deleted_count, _ = CeleryTaskRecord.objects.filter(
            created_at__lt=cutoff,
            status__in=[
                CeleryTaskRecord.TaskStatus.SUCCESS,
                CeleryTaskRecord.TaskStatus.FAILURE,
                CeleryTaskRecord.TaskStatus.REVOKED,
            ]
        ).delete()

        logger.info(f"[Celery Base] 清理了 {deleted_count} 条过期任务记录（{days}天前）")
        return {'status': 'success', 'deleted': deleted_count}
    except Exception as e:
        logger.error(f"[Celery Base] 清理任务记录失败: {e}")
        return {'status': 'error', 'error': str(e)}


@shared_task(
    bind=True,
    name='base.check_stale_tasks',
    max_retries=1,
    soft_time_limit=120,
)
def check_stale_tasks(self, timeout_minutes: int = 60):
    from django.utils import timezone
    from datetime import timedelta
    from Django_xm.apps.core.task_models import CeleryTaskRecord

    cutoff = timezone.now() - timedelta(minutes=timeout_minutes)

    try:
        stale_tasks = CeleryTaskRecord.objects.filter(
            status__in=[
                CeleryTaskRecord.TaskStatus.STARTED,
                CeleryTaskRecord.TaskStatus.PROGRESS,
                CeleryTaskRecord.TaskStatus.PENDING,
            ],
            started_at__lt=cutoff,
        )

        count = stale_tasks.count()
        for task in stale_tasks:
            task.mark_failure(error_message=f'任务超时（{timeout_minutes}分钟未完成）')

        logger.info(f"[Celery Base] 标记了 {count} 个超时任务")
        return {'status': 'success', 'marked_stale': count}
    except Exception as e:
        logger.error(f"[Celery Base] 检测超时任务失败: {e}")
        return {'status': 'error', 'error': str(e)}
