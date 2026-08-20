# DDL 迁移：去掉 JSONField 的 null=True
#
# 依赖 0002（纯 DML）之后执行。与 DML 拆分为独立迁移文件（各自独立事务），
# 避免 PostgreSQL “cannot ALTER TABLE ... because it has pending trigger events”。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0002_remove_jsonfield_null"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatmessage",
            name="approval",
            field=models.JSONField(blank=True, default=dict, verbose_name="审批数据"),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="chain_of_thought",
            field=models.JSONField(blank=True, default=list, verbose_name="思维链"),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="plan",
            field=models.JSONField(blank=True, default=dict, verbose_name="计划"),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="reasoning",
            field=models.JSONField(blank=True, default=dict, verbose_name="推理"),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="sources",
            field=models.JSONField(blank=True, default=list, verbose_name="来源"),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="suggestions",
            field=models.JSONField(blank=True, default=list, verbose_name="建议问题"),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="versions",
            field=models.JSONField(blank=True, default=list, verbose_name="消息版本"),
        ),
        migrations.AlterField(
            model_name="chatsession",
            name="selected_knowledge_bases",
            field=models.JSONField(
                blank=True, default=list, verbose_name="选中的知识库列表"
            ),
        ),
    ]