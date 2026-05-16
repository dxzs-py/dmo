"""
Django 信号处理模块 - 核心自定义信号定义

仅定义跨应用的自定义信号和用户/Celery相关信号。
各应用的模型信号处理已移至各自 apps 的 signals.py 中。

自定义信号:
  - cache_invalidated: 缓存失效通知
  - task_status_changed: 任务状态变更通知
  - index_updated: 索引更新通知
"""

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver, Signal

logger = logging.getLogger(__name__)


cache_invalidated = Signal()
task_status_changed = Signal()
index_updated = Signal()


@receiver(post_save, sender='users.User')
def user_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新用户创建: {instance.username} (id={instance.id})")
    else:
        logger.debug(f"用户更新: {instance.username} (id={instance.id})")


@receiver(post_delete, sender='users.User')
def user_post_delete(sender, instance, **kwargs):
    logger.info(f"用户删除: {instance.username} (id={instance.id})")


try:
    from Django_xm.apps.core.task_models import CeleryTaskRecord

    @receiver(post_save, sender=CeleryTaskRecord)
    def celery_task_record_post_save(sender, instance, created, **kwargs):
        if created:
            logger.info(f"Celery 任务记录创建: {instance.celery_task_id} ({instance.task_name})")
        else:
            if instance.status in ('success', 'failure', 'revoked'):
                logger.info(
                    f"Celery 任务完成: {instance.celery_task_id} "
                    f"status={instance.status} runtime={instance.runtime_seconds}s"
                )

        task_status_changed.send(
            sender=sender,
            task_id=instance.celery_task_id,
            status=instance.status,
            created=created,
        )
except Exception as e:
    logger.debug(f"CeleryTaskRecord 信号注册跳过: {e}")
