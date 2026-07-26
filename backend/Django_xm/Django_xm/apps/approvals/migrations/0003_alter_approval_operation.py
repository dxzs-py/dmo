"""把 operation 字段从 CharField(256) 改为 TextField。

shell_exec 等工具的命令可能远超 256 字符（例如带文件路径重定向的复合命令），
导致 approval_service.request_approval_async 持久化失败，进而使后续
/api/v1/approvals/{interrupt_id}/resume/ 因找不到记录而返回 400。
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('approvals', '0002_rename_approvals_source_source_id_idx_approvals_a_source_3872e0_idx_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='approval',
            name='operation',
            field=models.TextField(blank=True, default=''),
        ),
    ]
