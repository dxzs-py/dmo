"""
深度研究模块信号处理

负责 ResearchTask 的信号处理，包括:
- 缓存失效
- 任务状态变更通知
"""

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import ResearchTask

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ResearchTask)
def on_research_task_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新研究任务创建: {instance.task_id} (query={instance.query[:50]}...)")
    else:
        if instance.status == 'completed':
            logger.info(f"研究任务完成: {instance.task_id}")
            try:
                from Django_xm.apps.cache_manager.services.cache_service import CacheInvalidationStrategy
                CacheInvalidationStrategy.on_research_completed(instance.task_id)
            except Exception as e:
                logger.warning(f"研究缓存失效失败: {e}")
        elif instance.status == 'failed':
            logger.warning(f"研究任务失败: {instance.task_id}, 错误: {instance.error_message}")

    from Django_xm.apps.core.signals import task_status_changed
    task_status_changed.send(
        sender=sender,
        task_id=instance.task_id,
        status=instance.status,
        created=created,
    )


@receiver(post_delete, sender=ResearchTask)
def on_research_task_delete(sender, instance, **kwargs):
    logger.info(f"研究任务删除: {instance.task_id}")
