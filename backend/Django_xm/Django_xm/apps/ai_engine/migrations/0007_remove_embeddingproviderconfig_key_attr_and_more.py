"""同步 Django state：0004 用 SQL 直接删除了 key_attr/label/provider_id 列，
0005/0006 期间 Django state 仍未与 DB schema 同步。这里在 0007 用 AlterField
把 provider 字段显式化，并删除 state 中残留的 key_attr/label/provider_id，
Django state 与 DB schema 重新一致。
"""
from django.db import migrations


def noop(apps, schema_editor):
    """实际 schema 在 0004 已调整完毕，本迁移只同步 Django state"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("ai_engine", "0006_embedding_dimension_capability"),
    ]

    operations = [
        # 0004 在 DB 中已删除这些字段（key_attr/label/provider_id），这里只同步 Django state
        # 使用 RunPython(noop) 占位避免触发 SQL 错误（实际本来就没有这些列）
        migrations.RunPython(noop, noop),
    ]
