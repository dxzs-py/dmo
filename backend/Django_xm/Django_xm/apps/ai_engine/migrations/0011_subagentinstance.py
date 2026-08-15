# SubAgentRuntime：子代理实例元数据表

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ai_engine", "0010_systemconfig_created_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubAgentInstance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("thread_id", models.CharField(db_index=True, max_length=200, unique=True, verbose_name="子代理 thread_id")),
                (
                    "parent_thread_id",
                    models.CharField(
                        db_index=True,
                        max_length=200,
                        verbose_name="父线程 thread_id",
                        help_text="会话删除时按此字段遍历回收子代理",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "执行中"),
                            ("completed", "已完成"),
                            ("failed", "执行失败"),
                            ("interrupted_pending_user_input", "等待你的确认"),
                        ],
                        default="running",
                        max_length=50,
                        verbose_name="状态",
                    ),
                ),
                (
                    "pending_interrupt_info",
                    models.JSONField(
                        blank=True,
                        null=True,
                        verbose_name="中断信息",
                        help_text="interrupt 的审批 payload（interrupt_type/tool_call_id/tool_name/args/risk_level/reason）",
                    ),
                ),
                ("result_preview", models.TextField(blank=True, default="", verbose_name="结果预览")),
                ("metadata", models.JSONField(default=dict, blank=True, verbose_name="元数据")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "db_table": "ai_engine_subagent_instance",
                "verbose_name": "子代理实例",
                "verbose_name_plural": "子代理实例",
                "ordering": ["-created_at"],
            },
        ),
    ]
