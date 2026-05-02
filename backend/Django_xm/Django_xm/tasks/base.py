"""
基础 Celery 任务
存放通用、调试类的任务，以及任务追踪基类
"""
import logging
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


class TrackedTask:
    """
    可追踪的 Celery 任务混入类

    在任务执行过程中自动同步状态到数据库，
    实现任务进度追踪和历史查询

    用法:
        @shared_task(bind=True)
        def my_task(self, **kwargs):
            tracker = TrackedTask(self)
            tracker.mark_started()
            tracker.update_progress(50, "处理中...")
            ...
            tracker.mark_success(result={"key": "value"})
    """

    def __init__(self, celery_task):
        self.celery_task = celery_task
        self._record = None

    def _get_or_create_record(self):
        if self._record is not None:
            return self._record

        from Django_xm.apps.core.task_models import CeleryTaskRecord

        task_id = self.celery_task.request.id
        task_name = self.celery_task.name

        try:
            self._record = CeleryTaskRecord.objects.get(celery_task_id=task_id)
        except CeleryTaskRecord.DoesNotExist:
            self._record = CeleryTaskRecord.objects.create(
                celery_task_id=task_id,
                task_name=task_name,
                task_kwargs=self.celery_task.request.kwargs or {},
            )

        return self._record

    def mark_started(self, worker_name: str = ''):
        record = self._get_or_create_record()
        record.mark_started(worker_name=worker_name)

    def update_progress(self, progress: int, message: str = ''):
        record = self._get_or_create_record()
        record.mark_progress(progress, message)

    def mark_success(self, result=None):
        record = self._get_or_create_record()
        record.mark_success(result=result)

    def mark_failure(self, error_message: str = ''):
        record = self._get_or_create_record()
        record.mark_failure(error_message=error_message)

    def set_task_type(self, task_type: str):
        record = self._get_or_create_record()
        from Django_xm.apps.core.task_models import CeleryTaskRecord
        try:
            task_type_enum = CeleryTaskRecord.TaskType(task_type)
            record.task_type = task_type_enum
            record.save(update_fields=['task_type', 'updated_at'])
        except ValueError:
            pass

    def set_created_by(self, user_id: int):
        record = self._get_or_create_record()
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            record.created_by = User.objects.get(id=user_id)
            record.save(update_fields=['created_by', 'updated_at'])
        except User.DoesNotExist:
            pass


@shared_task(bind=True)
def debug_task(self):
    logger.info(f"[Celery] 调试任务执行，请求：{self.request!r}")
    return {'status': 'success', 'request': repr(self.request)}


@shared_task(bind=True)
def cleanup_old_task_records(self, days: int = 30):
    """
    清理过期的任务记录

    Args:
        days: 保留天数，默认30天
    """
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

        logger.info(f"清理了 {deleted_count} 条过期任务记录（{days}天前）")
        return {'deleted': deleted_count}
    except Exception as e:
        logger.error(f"清理任务记录失败: {e}")
        return {'error': str(e)}


@shared_task(bind=True)
def check_stale_tasks(self, timeout_minutes: int = 60):
    """
    检测并标记超时未完成的任务

    Args:
        timeout_minutes: 超时分钟数，默认60分钟
    """
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

        logger.info(f"标记了 {count} 个超时任务")
        return {'marked_stale': count}
    except Exception as e:
        logger.error(f"检测超时任务失败: {e}")
        return {'error': str(e)}
