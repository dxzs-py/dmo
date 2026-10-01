"""
深度研究模块信号处理

负责 ResearchTask 的信号处理，包括:
- 任务状态日志记录
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import ResearchTask

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ResearchTask)
def on_research_task_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新研究任务创建: {instance.task_id} (query={instance.query[:50]}...)")
    elif instance.status == "completed":
        logger.info(f"研究任务完成: {instance.task_id}")
    elif instance.status == "failed":
        logger.warning(f"研究任务失败: {instance.task_id}, 错误: {instance.error_message}")
    # 原 core 自定义信号 task_status_changed 的发送已移除（2026-10-01）：
    # 该 Django Signal 全仓无接收方，属死代码；任务状态的前端通知
    # 由 realtime 事件链（writeback.py 经 EventType.TASK_STATUS_CHANGED）承担。


@receiver(post_delete, sender=ResearchTask)
def on_research_task_delete(sender, instance, **kwargs):
    logger.info(f"研究任务删除: {instance.task_id}")
