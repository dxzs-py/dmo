# DML 迁移：先将存量 NULL 归一为空容器
#
# 与 0003_remove_jsonfield_null_ddl 拆分为两个独立迁移文件（各自独立事务），
# 避免 PostgreSQL “cannot ALTER TABLE ... because it has pending trigger events”。
# RUNPYTHON 的事务先提交，清除挂起的约束触发事件后，再在 0003 中对表执行 DDL。

from django.db import migrations, models


def normalize_jsonfield_null(apps, schema_editor):
    """dj-18：存量 NULL 归一为空容器。

    list 字段 → []，dict 字段 → {}。先于 AlterField 去掉 null=True 执行，
    保证非空约束在存量库上成立（__isnull 命中 SQL NULL）。
    """
    ChatSession = apps.get_model("chat", "ChatSession")
    ChatMessage = apps.get_model("chat", "ChatMessage")

    ChatSession.objects.filter(selected_knowledge_bases__isnull=True).update(selected_knowledge_bases=[])

    list_fields = ("sources", "chain_of_thought", "suggestions", "versions")
    dict_fields = ("plan", "approval", "reasoning")
    for field in list_fields:
        ChatMessage.objects.filter(**{f"{field}__isnull": True}).update(**{field: []})
    for field in dict_fields:
        ChatMessage.objects.filter(**{f"{field}__isnull": True}).update(**{field: {}})


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0001_squashed"),
    ]

    operations = [
        migrations.RunPython(normalize_jsonfield_null, migrations.RunPython.noop),
    ]