"""
Celery 信号处理模块
监听 Celery 内置信号，实现全局任务状态追踪、异常告警和资源清理
"""
import logging

from celery.signals import (
    task_prerun,
    task_postrun,
    task_failure,
    task_retry,
    task_revoked,
    task_unknown,
    task_rejected,
    worker_ready,
    worker_shutting_down,
)

logger = logging.getLogger(__name__)


@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, task=None, args=None, kwargs=None, **extra):
    logger.debug(f"[Celery Signal] 任务开始: {task.name}[{task_id}]")


@task_postrun.connect
def on_task_postrun(sender=None, task_id=None, task=None, args=None, kwargs=None,
                    retval=None, state=None, **extra):
    logger.debug(f"[Celery Signal] 任务结束: {task.name}[{task_id}] state={state}")


@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, traceback_str=None,
                    args=None, kwargs=None, einfo=None, **extra):
    task_name = sender.name if sender else 'unknown'
    tb = traceback_str or (str(einfo) if einfo else '')
    logger.error(
        f"[Celery Signal] 任务失败: {task_name}[{task_id}] "
        f"exception={exception} traceback={tb}"
    )

    try:
        from Django_xm.apps.core.task_models import CeleryTaskRecord
        record = CeleryTaskRecord.objects.filter(celery_task_id=task_id).first()
        if record and record.status not in (
            CeleryTaskRecord.TaskStatus.FAILURE,
            CeleryTaskRecord.TaskStatus.REVOKED,
        ):
            record.mark_failure(error_message=str(exception))
    except Exception as e:
        logger.debug(f"[Celery Signal] 同步失败状态到数据库异常: {e}")


@task_retry.connect
def on_task_retry(sender=None, task_id=None, reason=None, einfo=None, **extra):
    task_name = sender.name if sender else 'unknown'
    logger.warning(
        f"[Celery Signal] 任务重试: {task_name}[{task_id}] reason={reason}"
    )

    try:
        from Django_xm.apps.core.task_models import CeleryTaskRecord
        record = CeleryTaskRecord.objects.filter(celery_task_id=task_id).first()
        if record:
            retry_count = record.retry_count + 1
            record.mark_retry(retry_count=retry_count)
    except Exception as e:
        logger.debug(f"[Celery Signal] 同步重试状态到数据库异常: {e}")


@task_revoked.connect
def on_task_revoked(sender=None, request=None, terminated=None, signum=None, expired=None, **extra):
    task_id = request.id if request else None
    task_name = request.name if request else 'unknown'
    reason = 'expired' if expired else ('terminated' if terminated else 'revoked')
    logger.warning(
        f"[Celery Signal] 任务撤销: {task_name}[{task_id}] reason={reason}"
    )

    if not task_id:
        return

    try:
        from Django_xm.apps.core.task_models import CeleryTaskRecord
        record = CeleryTaskRecord.objects.filter(celery_task_id=task_id).first()
        if record and record.status not in (
            CeleryTaskRecord.TaskStatus.SUCCESS,
            CeleryTaskRecord.TaskStatus.FAILURE,
        ):
            record.mark_revoked()
    except Exception as e:
        logger.debug(f"[Celery Signal] 同步撤销状态到数据库异常: {e}")


@task_unknown.connect
def on_task_unknown(sender=None, name=None, id=None, message=None, exc=None, **extra):
    logger.error(f"[Celery Signal] 未知任务: name={name} id={id}")


@task_rejected.connect
def on_task_rejected(sender=None, message=None, exc=None, **extra):
    logger.error(f"[Celery Signal] 任务被拒绝: {message}")


@worker_ready.connect
def on_worker_ready(sender=None, **extra):
    logger.info("[Celery Signal] Worker 已就绪")


@worker_shutting_down.connect
def on_worker_shutting_down(sender=None, sig=None, how=None, exitcode=None, **extra):
    logger.info(f"[Celery Signal] Worker 正在关闭: signal={sig} how={how}")
