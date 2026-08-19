"""
学习工作流信号处理

负责 WorkflowSession 的 user 频道实时事件发布（与深度研究模块 research/signals.py 对齐）：
- 创建 → task_created：通知所有浏览器历史任务列表出现新行（无需手动刷新）
- 状态进入终态（completed/failed）→ task_status_changed：通知列表状态同步更新
- 删除 → task_deleted：通知列表移除该行

统一发布到 user 频道，前端 useTaskListRealtimeSync（学习工作流 / 深度研究公共能力）
消费后自动刷新任务列表，实现跨浏览器实时同步。
"""

import logging

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(pre_save, sender="learning.WorkflowSession")
def workflow_session_pre_save(sender, instance, **kwargs):
    """记录保存前状态、步骤与删除标记，供 post_save 判断是否发生变化（避免每次更新重复发布）。"""
    instance._prev_status = None
    instance._prev_current_step = None
    instance._prev_is_deleted = None
    if instance.pk:
        try:
            old = (
                sender.objects.filter(pk=instance.pk)
                .values_list("status", "current_step", "is_deleted")
                .first()
            )
            if old:
                instance._prev_status, instance._prev_current_step, instance._prev_is_deleted = old
        except Exception:
            instance._prev_status = None
            instance._prev_current_step = None
            instance._prev_is_deleted = None


@receiver(post_save, sender="learning.WorkflowSession")
def workflow_session_post_save(sender, instance, created, **kwargs):
    """创建 → task_created；状态/步骤变化 → task_status_changed（均发到 user 频道）。

    状态/步骤变化即发布（含 waiting_for_answers 中断等待）：学习工作流的
    waiting_for_answers 是用户可见的重要状态（等待答题），列表需同步更新，
    与深度研究模块在终态才刷新的行为对齐但更实时。
    """
    try:
        from Django_xm.common.event_schema import EventType
        from Django_xm.common.realtime_events import publish_event_sync

        user_id = instance.created_by_id
        if not user_id:
            return
        # 软删除（is_deleted False→True）：soft_delete() 走 save() 触发 post_save，
        # post_delete 信号对软删除不触发，故在此检测删除标记变化发布 task_deleted。
        if instance._prev_is_deleted is not None and not instance._prev_is_deleted and instance.is_deleted:
            publish_event_sync(
                EventType.TASK_DELETED,
                {"task_id": instance.thread_id},
                user_id=str(user_id),
            )
            return
        if created:
            publish_event_sync(
                EventType.TASK_CREATED,
                {"task_id": instance.thread_id},
                user_id=str(user_id),
            )
        elif (
            instance.status != instance._prev_status
            or instance.current_step != instance._prev_current_step
        ):
            publish_event_sync(
                EventType.TASK_STATUS_CHANGED,
                {
                    "task_id": instance.thread_id,
                    "status": instance.status,
                    "current_step": instance.current_step,
                },
                user_id=str(user_id),
            )
    except Exception as e:
        logger.warning(f"[Learning] 发布列表实时事件失败: thread_id={instance.thread_id}, error={e}")


@receiver(post_delete, sender="learning.WorkflowSession")
def workflow_session_post_delete(sender, instance, **kwargs):
    """删除 → task_deleted（user 频道），通知所有浏览器移除列表行。"""
    try:
        from Django_xm.common.event_schema import EventType
        from Django_xm.common.realtime_events import publish_event_sync

        if instance.created_by_id:
            publish_event_sync(
                EventType.TASK_DELETED,
                {"task_id": instance.thread_id},
                user_id=str(instance.created_by_id),
            )
    except Exception as e:
        logger.warning(f"[Learning] 发布 task_deleted 事件失败: thread_id={instance.thread_id}, error={e}")
