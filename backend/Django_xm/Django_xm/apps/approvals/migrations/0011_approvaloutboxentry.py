# Generated for Phase C: Transactional Outbox

from django.db import migrations, models

import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("approvals", "0010_approval_approval_src_state_created_idx"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApprovalOutboxEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "event_type",
                    models.CharField(max_length=64, verbose_name="事件类型"),
                ),
                (
                    "payload",
                    models.JSONField(default=dict, verbose_name="事件载荷"),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("delivered", "Delivered"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="投递状态",
                    ),
                ),
                (
                    "attempts",
                    models.IntegerField(default=0, verbose_name="重试次数"),
                ),
                (
                    "max_attempts",
                    models.IntegerField(default=3, verbose_name="最大重试次数"),
                ),
                (
                    "next_retry_at",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        null=True,
                        verbose_name="下次重试时间",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "delivered_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="投递时间",
                    ),
                ),
                (
                    "error_message",
                    models.TextField(
                        blank=True,
                        default="",
                        verbose_name="错误信息",
                    ),
                ),
                (
                    "approval",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outbox_entries",
                        to="approvals.approval",
                        verbose_name="关联审批",
                    ),
                ),
            ],
            options={
                "verbose_name": "审批发件箱条目",
                "verbose_name_plural": "审批发件箱条目",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="approvaloutboxentry",
            index=models.Index(
                fields=["state", "next_retry_at"],
                name="approval_outbox_state_retry_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="approvaloutboxentry",
            index=models.Index(
                fields=["approval", "event_type"],
                name="approval_outbox_apv_evt_idx",
            ),
        ),
    ]
