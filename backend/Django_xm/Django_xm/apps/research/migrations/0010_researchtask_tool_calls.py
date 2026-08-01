"""恢复 ResearchTask.tool_calls 字段。

将工具调用历史持久化从 Approval 模型解耦回 ResearchTask.tool_calls：
- Approval 仅记录需要人工审批的工具调用
- tool_calls 记录全部工具调用（含自动通过）的生命周期状态

根本解决 issue_audit_15：自动通过的工具调用从 tool_calls 查询，
不再因无 Approval 记录而丢失。

依赖 0009（0009 删除了 tool_calls，本迁移恢复）。
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("research", "0009_remove_researchtask_approval_history_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="researchtask",
            name="tool_calls",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="对象结构，key=tool_call_id，value=工具调用详情",
                verbose_name="工具调用历史",
            ),
        ),
    ]
