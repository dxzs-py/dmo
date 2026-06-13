"""
Research 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 research.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""
import logging

from django.apps import apps

logger = logging.getLogger(__name__)


def get_linked_research_tasks(session_id):
    """获取会话关联的研究任务摘要列表 [{'task_id': ..., 'query': ...}]"""
    ResearchTask = apps.get_model('research', 'ResearchTask')
    return list(ResearchTask.objects.filter(
        session_id=session_id, is_deleted=False
    ).values('task_id', 'query'))


def get_linked_research_tasks_including_deleted(session_id):
    """获取会话关联的研究任务（包括已软删除的），用于清理判断"""
    ResearchTask = apps.get_model('research', 'ResearchTask')
    return list(ResearchTask.all_objects.filter(
        session_id=session_id
    ).values('task_id', 'query', 'is_deleted'))


def get_user_research_task_ids(user_id):
    """获取用户的研究任务 ID 集合"""
    ResearchTask = apps.get_model('research', 'ResearchTask')
    return set(ResearchTask.objects.filter(
        created_by_id=user_id, is_deleted=False
    ).values_list('task_id', flat=True))


def update_research_task_model_and_tokens(task_id, model_name='', token_count=0, token_detail=None, response_time=0):
    """更新研究任务的模型和 Token 信息"""
    ResearchTask = apps.get_model('research', 'ResearchTask')
    update_fields = {'model': model_name, 'token_count': token_count, 'response_time': response_time}
    if token_detail:
        update_fields['token_detail'] = token_detail
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
        pass
    return []


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
    ResearchTask = apps.get_model('research', 'ResearchTask')
    # 查找包括已软删除的任务（必须用 all_objects，默认 objects 过滤了 is_deleted=True）
    task = ResearchTask.all_objects.filter(task_id=task_id, created_by_id=user_id).first()
    if task is None or not task.is_deleted:
        # 任务不存在或未删除，不清理
        return

    # 检查是否仍有活跃聊天会话关联此研究任务
    ChatMessage = apps.get_model('chat', 'ChatMessage')
    active_session_ids = ChatMessage.all_objects.filter(
        research_task_id=task_id,
        session__is_deleted=False,
    ).values_list('session__session_id', flat=True)
    if active_session_ids.exists():
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
            delete_thread_checkpoints, delete_thread_store_data,
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
