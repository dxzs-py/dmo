"""
Learning 跨 app 服务层 - 供其他 app 调用的接口

解耦其他 app 对 learning.models 的直接导入，通过薄封装的 ORM 查询提供服务。
"""
from django.apps import apps


def get_user_workflow_count(user):
    """获取用户的工作流数量"""
    WorkflowSession = apps.get_model('learning', 'WorkflowSession')
    return WorkflowSession.objects.filter(created_by=user, is_deleted=False).count()
