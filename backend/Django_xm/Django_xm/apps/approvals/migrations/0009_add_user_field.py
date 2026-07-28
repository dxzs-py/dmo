"""Approval 增加 user 外键字段，并通过三路关联回填历史数据。

回填路径（参考 apps/approvals/views.py:_user_owns_approval 的三路分支）：
1. chat_session_id → ChatSession.session_id → ChatSession.user
2. source='deep_research' 时 source_id → ResearchTask.task_id → ResearchTask.created_by
3. source='learning' 时 source_id → WorkflowSession.thread_id → WorkflowSession.created_by

回填后 NULL 数据由 BaseApprovalAccessMixin._user_owns_approval 三路 fallback 兜底。
"""

from django.conf import settings
from django.db import migrations, models


def backfill_approval_user(apps, schema_editor):
    """通过三路关联回填 Approval.user 字段。

    优先级：chat_session_id > deep_research > learning。
    仅回填可确定归属的记录；无法确定归属的保留 NULL，由 fallback 处理。
    """
    Approval = apps.get_model('approvals', 'Approval')
    ChatSession = apps.get_model('chat', 'ChatSession')
    ResearchTask = apps.get_model('research', 'ResearchTask')
    WorkflowSession = apps.get_model('learning', 'WorkflowSession')

    pending = Approval.objects.filter(user__isnull=True)
    for approval in pending.iterator():
        owner_id = None

        # 路径 1：chat_session_id → ChatSession.user
        if approval.chat_session_id:
            cs = (
                ChatSession.objects.filter(session_id=approval.chat_session_id)
                .exclude(user__isnull=True)
                .first()
            )
            if cs is not None:
                owner_id = cs.user_id

        # 路径 2：source='deep_research' → ResearchTask.created_by
        if owner_id is None and approval.source == 'deep_research':
            rt = (
                ResearchTask.objects.filter(task_id=approval.source_id)
                .exclude(created_by__isnull=True)
                .first()
            )
            if rt is not None:
                owner_id = rt.created_by_id

        # 路径 3：source='learning' → WorkflowSession.created_by
        if owner_id is None and approval.source == 'learning':
            ws = (
                WorkflowSession.objects.filter(thread_id=approval.source_id)
                .exclude(created_by__isnull=True)
                .first()
            )
            if ws is not None:
                owner_id = ws.created_by_id

        if owner_id is not None:
            approval.user_id = owner_id
            approval.save(update_fields=['user'])


def noop_reverse(apps, schema_editor):
    """回滚不还原数据，仅由 RemoveField 处理字段移除。"""
    pass


class Migration(migrations.Migration):
    """新增 user 外键字段 + 复合索引，并回填历史数据。"""

    dependencies = [
        ('approvals', '0008_approval_expires_at_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # 回填依赖：三路关联需要 ChatSession / ResearchTask / WorkflowSession 模型已建立
        ('chat', '0013_add_selected_tools_to_session'),
        ('research', '0008_researchtask_approval_history_and_more'),
        ('learning', '0008_alter_workflowsession_knowledge_base_ids_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='approval',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name='owned_approvals',
                to=settings.AUTH_USER_MODEL,
                verbose_name='审批归属用户',
            ),
        ),
        migrations.RunPython(backfill_approval_user, noop_reverse),
        migrations.AddIndex(
            model_name='approval',
            index=models.Index(
                fields=['user', 'state', 'created_at'],
                name='approvals_a_user_id_df0012_idx',
            ),
        ),
    ]
