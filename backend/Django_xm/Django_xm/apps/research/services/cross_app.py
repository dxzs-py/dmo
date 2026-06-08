"""
Research 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 research.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""
from django.apps import apps


def get_linked_research_tasks(session_id):
    """获取会话关联的研究任务摘要列表 [{'task_id': ..., 'query': ...}]"""
    ResearchTask = apps.get_model('research', 'ResearchTask')
    return list(ResearchTask.objects.filter(
        session_id=session_id, is_deleted=False
    ).values('task_id', 'query'))


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
