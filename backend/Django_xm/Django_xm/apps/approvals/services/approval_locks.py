"""审批分布式锁与 Redis 工具模块（从 approval_service.py 拆分，Task 15.1）。

集中管理审批流程中所有 Redis 分布式锁的常量与获取/释放逻辑，以及任务终态检测：
- 审批操作锁（APPROVAL_LOCK_PREFIX）：防止并发 resume/timeout 同一审批
- 任务恢复锁（TASK_RESUME_LOCK_PREFIX）：防止并发恢复同一深度研究任务
- 恢复锁（RESUME_LOCK_PREFIX）：深度研究 SETNX 乐观锁，防止并发恢复

锁获取统一使用 SET NX EX（原子操作），释放使用 DEL。
所有锁均带 TTL，防止持锁进程崩溃导致死锁。
"""

import logging

from asgiref.sync import sync_to_async
from django.core.cache import cache

logger = logging.getLogger(__name__)

APPROVAL_LOCK_TTL = 300
APPROVAL_LOCK_PREFIX = "approval:lock:"
TASK_RESUME_LOCK_PREFIX = "approval:task_resume:"
TASK_RESUME_LOCK_TTL = 1800
RESUME_LOCK_PREFIX = "approval:resume_lock:"
RESUME_LOCK_TTL = 30


def _get_redis_client():
    return cache.client.get_client()


def _acquire_lock(interrupt_id: str) -> bool:
    redis_client = _get_redis_client()
    lock_key = f"{APPROVAL_LOCK_PREFIX}{interrupt_id}"
    return bool(redis_client.set(lock_key, "1", nx=True, ex=APPROVAL_LOCK_TTL))


def release_lock(interrupt_id: str):
    redis_client = _get_redis_client()
    lock_key = f"{APPROVAL_LOCK_PREFIX}{interrupt_id}"
    redis_client.delete(lock_key)


release_lock_async = sync_to_async(release_lock)


def _acquire_task_resume_lock(task_id: str) -> bool:
    redis_client = _get_redis_client()
    lock_key = f"{TASK_RESUME_LOCK_PREFIX}{task_id}"
    return bool(redis_client.set(lock_key, "1", nx=True, ex=TASK_RESUME_LOCK_TTL))


def _release_task_resume_lock(task_id: str):
    redis_client = _get_redis_client()
    lock_key = f"{TASK_RESUME_LOCK_PREFIX}{task_id}"
    redis_client.delete(lock_key)


def _is_task_in_terminal_state(task_id: str) -> bool:
    try:
        from django.apps import apps as _apps

        ResearchTask = _apps.get_model("research", "ResearchTask")
        from Django_xm.common.enums import TaskStatus

        terminal_states = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
        return ResearchTask.objects.filter(
            task_id=task_id,
            status__in=terminal_states,
            is_deleted=False,
        ).exists()
    except Exception as e:
        logger.warning(f"[ApprovalService] 检查任务终态失败: task_id={task_id}, err={e}")
        return False
