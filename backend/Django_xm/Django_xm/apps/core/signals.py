"""
Django 信号处理模块 - 核心自定义信号定义

仅定义跨应用的自定义信号和用户/Celery相关信号。
各应用的模型信号处理已移至各自 apps 的 signals.py 中。

自定义信号:
  - ai_data_cleanup_needed: AI 数据（checkpoint/Store）清理通知
    （原 task_status_changed 因全仓无接收方、场景已被 realtime 事件总线
     EventType.TASK_STATUS_CHANGED 覆盖，2026-10-01 移除）
  - knowledge_base_deleted: 知识库删除完成通知（A-2 依赖反转，2026-10-06）。
    发送方 knowledge.kb_service.delete_knowledge_base；接收方 chat
    （清理会话中对该库的选中引用）。参数：user_id: int, kb_name: str
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import Signal, receiver

logger = logging.getLogger(__name__)


ai_data_cleanup_needed = Signal()

# A-2：knowledge→chat 上行依赖反转为事件通知——资源层 app 发信号，
# 业务层 app 自行订阅，knowledge 不再 import chat.cross_app
knowledge_base_deleted = Signal()

@receiver(post_save, sender="users.User")
def user_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"新用户创建: {instance.username} (id={instance.id})")
    else:
        logger.debug(f"用户更新: {instance.username} (id={instance.id})")


@receiver(post_delete, sender="users.User")
def user_post_delete(sender, instance, **kwargs):
    logger.info(f"用户删除: {instance.username} (id={instance.id})")

    # 通过自定义信号通知 ai_engine 清理用户数据，避免硬导入
    ai_data_cleanup_needed.send(
        sender=sender,
        user_id=instance.id,
        session_id=None,
    )


@receiver(post_save, sender="core.CeleryTaskRecord")
def celery_task_record_post_save(sender, instance, created, **kwargs):
    if created:
        logger.info(f"Celery 任务记录创建: {instance.celery_task_id} ({instance.task_name})")
    elif instance.status in ("success", "failure", "revoked"):
        logger.info(
            f"Celery 任务完成: {instance.celery_task_id} "
            f"status={instance.status} runtime={instance.runtime_seconds}s"
        )
