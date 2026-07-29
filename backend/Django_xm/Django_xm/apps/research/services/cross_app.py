"""
Research 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 research.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""

import logging

from django.apps import apps

logger = logging.getLogger(__name__)


# ── 研究任务 Redis 通道前缀（供 chat 应用订阅研究结果/审批事件） ──────────
# 定义在 research_runner.py，此处通过 facade 重新导出，
# 供 chat 应用通过 cross_app 门面访问，避免 chat → research_runner 直接依赖。
#
# 使用 __getattr__ 懒加载：research_runner.py 顶层 import token_counter，
# 若在 app 初始化期间被 eager import 会触发 ai_engine 模块链加载，
# 此时 approvals 等 app 可能尚未就绪导致 AppRegistryNotReady。
# __getattr__ 确保 chat 顶层 `from cross_app import REDIS_CHANNEL_PREFIX`
# 不再在 app 加载阶段触发 research_runner 的加载。

_REDIS_CONSTANTS = {
    "REDIS_CHANNEL_PREFIX",
    "REDIS_APPROVAL_PREFIX",
    "REDIS_APPROVAL_RESPONSE_PREFIX",
}


def __getattr__(name):
    if name in _REDIS_CONSTANTS:
        from . import research_runner

        return getattr(research_runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "REDIS_APPROVAL_PREFIX",  # noqa: F822  懒加载（见 __getattr__）
    "REDIS_APPROVAL_RESPONSE_PREFIX",  # noqa: F822  懒加载（见 __getattr__）
    "REDIS_CHANNEL_PREFIX",  # noqa: F822  懒加载（见 __getattr__）
    "cleanup_research_if_both_deleted",
    "get_linked_research_tasks",
    "get_linked_research_tasks_including_deleted",
    "get_research_task_for_context",
    "get_research_task_manager",
    "get_research_version_chain",
    "get_user_research_task_ids",
    "update_research_task_fields",
    "update_research_task_model_and_tokens",
    "user_owns_research_task",
]


def get_linked_research_tasks(session_id):
    """获取会话关联的研究任务摘要列表 [{'task_id': ..., 'query': ...}]"""
    ResearchTask = apps.get_model("research", "ResearchTask")
    return list(ResearchTask.objects.filter(session_id=session_id, is_deleted=False).values("task_id", "query"))


def get_linked_research_tasks_including_deleted(session_id):
    """获取会话关联的研究任务（包括已软删除的），用于清理判断"""
    ResearchTask = apps.get_model("research", "ResearchTask")
    return list(ResearchTask.all_objects.filter(session_id=session_id).values("task_id", "query", "is_deleted"))


def get_user_research_task_ids(user_id):
    """获取用户的研究任务 ID 集合"""
    ResearchTask = apps.get_model("research", "ResearchTask")
    return set(ResearchTask.objects.filter(created_by_id=user_id, is_deleted=False).values_list("task_id", flat=True))


def update_research_task_model_and_tokens(task_id, model_name="", token_count=0, token_detail=None, response_time=0):
    """更新研究任务的模型和 Token 信息"""
    ResearchTask = apps.get_model("research", "ResearchTask")
    update_fields = {"model": model_name, "token_count": token_count, "response_time": response_time}
    if token_detail:
        update_fields["token_detail"] = token_detail
    ResearchTask.objects.filter(task_id=task_id).update(**update_fields)


# ── 供 chat 应用调用的服务封装（消除循环依赖） ──────────────────────


def get_research_task_manager():
    """供 chat 应用调用：获取研究任务管理器"""
    from .task_manager import get_task_manager

    return get_task_manager()


def get_research_version_chain(task_id: str) -> list:
    """获取研究任务的版本链"""
    from Django_xm.apps.research.models import ResearchTask

    try:
        task = ResearchTask.objects.filter(task_id=task_id, is_deleted=False).first()
        if task:
            return task.version_chain
    except Exception:
        # 数据库读取失败时返回空列表，不影响调用方
        logger.debug("获取研究任务 %s 版本链失败", task_id)
    return []


def user_owns_research_task(task_id: str, user) -> bool:
    """校验用户是否为指定研究任务的创建者（含 is_deleted=False 过滤）。

    供 chat consumers 的 task 通道订阅/回放权限校验使用。

    Args:
        task_id: 研究任务 ID
        user: 用户对象

    Returns:
        bool: 用户拥有该任务返回 True，否则 False（含异常兜底）
    """
    ResearchTask = apps.get_model("research", "ResearchTask")
    try:
        return ResearchTask.objects.filter(task_id=task_id, created_by=user, is_deleted=False).exists()
    except Exception as e:
        logger.warning(f"[CrossApp] user_owns_research_task 校验失败: task_id={task_id}, {e}")
        return False


def update_research_task_fields(task_id: str, **fields) -> None:
    """更新研究任务字段（通用更新接口）。

    供 chat 应用在创建/提交深度研究任务时更新 knowledge_base_ids、celery_task_id 等字段。

    Args:
        task_id: 研究任务 ID
        **fields: 任意可更新的模型字段（如 knowledge_base_ids=..., celery_task_id=...）
    """
    if not fields:
        return
    ResearchTask = apps.get_model("research", "ResearchTask")
    ResearchTask.objects.filter(task_id=task_id).update(**fields)


def get_research_task_for_context(
    research_task_id: str,
    user_id: int | None = None,
) -> dict | None:
    """获取研究任务用于聊天上下文加载（包括已软删除的任务）。

    用 all_objects 查询，已软删除的研究任务仍可被聊天引用（只要聊天会话还在）。

    Args:
        research_task_id: 研究任务 ID
        user_id: 用户 ID（可选，传入则做归属校验）

    Returns:
        dict: {'task_id': str, 'query': str, 'final_report': str} 或 None
    """
    ResearchTask = apps.get_model("research", "ResearchTask")
    qs = ResearchTask.all_objects.filter(task_id=research_task_id)
    if user_id:
        qs = qs.filter(created_by_id=user_id)
    task = qs.first()
    if not task:
        return None
    return {
        "task_id": task.task_id,
        "query": task.query or "",
        "final_report": task.final_report or "",
    }


def cleanup_research_if_both_deleted(task_id: str, user_id: int):
    """检查深度研究任务是否已软删除，且无活跃聊天会话关联，满足条件则清理后端数据

    在聊天会话删除时调用，实现"双方都删才清理"策略：
    - 深度研究已删除 + 所有关联聊天都已删除 → 清理后端数据
    - 深度研究未删除 → 不清理（研究侧仍需查看）
    - 深度研究已删除 + 仍有活跃聊天关联 → 不清理（其他聊天仍需引用）

    Args:
        task_id: 研究任务 ID
        user_id: 用户 ID
    """
    ResearchTask = apps.get_model("research", "ResearchTask")
    # 查找包括已软删除的任务（必须用 all_objects，默认 objects 过滤了 is_deleted=True）
    task = ResearchTask.all_objects.filter(task_id=task_id, created_by_id=user_id).first()
    if task is None or not task.is_deleted:
        # 任务不存在或未删除，不清理
        return

    # 检查是否仍有活跃聊天会话关联此研究任务
    # 通过 chat 应用 cross_app 门面访问 ChatMessage，消除 research → chat.models 直接依赖
    from Django_xm.apps.chat.services.cross_app import get_active_session_ids_for_research_task

    active_session_ids = get_active_session_ids_for_research_task(task_id)
    if active_session_ids:
        logger.info(f"深度研究 {task_id} 已删除，但仍有活跃聊天关联，保留后端数据")
        return

    # 深度研究已删除，且无活跃聊天关联，清理后端数据
    logger.info(f"深度研究 {task_id} 和所有关联聊天均已删除，清理后端数据")
    _cleanup_research_backend_data(task_id, user_id)


def _cleanup_research_backend_data(task_id: str, user_id: int):
    """清理深度研究的后端数据：checkpoint、store、磁盘文件"""
    # 清理 checkpoint 和 store 数据
    try:
        from Django_xm.apps.ai_engine.services.checkpointer_factory import (
            delete_thread_checkpoints,
            delete_thread_store_data,
        )
        from Django_xm.async_utils import run_async

        async def _cleanup():
            await delete_thread_checkpoints(task_id)
            await delete_thread_store_data(user_id, task_id)

        run_async(_cleanup())
        logger.info(f"研究任务 {task_id} 的 checkpoint/Store 数据已清理")
    except Exception as e:
        logger.warning(f"研究任务 {task_id} 清理 checkpoint/Store 数据失败: {e}")

    # 清理磁盘文件
    try:
        from Django_xm.apps.core.services.file_manager import get_file_manager

        file_manager = get_file_manager()
        file_manager.delete_task_files(task_id, "research")
    except Exception as e:
        logger.warning(f"研究任务 {task_id} 清理磁盘文件失败: {e}")
