"""
学习工作流模块信号处理

负责 WorkflowSession 和 WorkflowExecution 的信号处理。
"""

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import WorkflowSession, WorkflowExecution

logger = logging.getLogger(__name__)


@receiver(post_save, sender=WorkflowSession)
def on_workflow_session_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新工作流会话创建: {instance.thread_id} (question={instance.user_question[:50]}...)")
    else:
        if instance.status == 'completed':
            logger.info(f"工作流会话完成: {instance.thread_id}")
        elif instance.status == 'failed':
            logger.warning(f"工作流会话失败: {instance.thread_id}, 错误: {instance.error_message}")


@receiver(post_delete, sender=WorkflowSession)
def on_workflow_session_delete(sender, instance, **kwargs):
    logger.info(f"工作流会话删除: {instance.thread_id}")


@receiver(post_save, sender=WorkflowExecution)
def on_workflow_execution_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新工作流执行记录创建: {instance.thread_id} (type={instance.workflow_type})")
    else:
        logger.debug(f"工作流执行记录更新: {instance.thread_id}")
