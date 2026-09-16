# DML 迁移：存量 ResearchTask 的 enable_sandbox 字段回填默认值 False
#
# 与 0017_researchtask_enable_sandbox（DDL：AddField）拆分为两个独立迁移文件
# （各自独立事务），避免 PostgreSQL "cannot ALTER TABLE ... because it has
# pending trigger events"。
#
# 语义说明：沙箱开关默认关闭（任务级开关，用户自主选择），
# 存量任务一律回填 False，保证与新建任务缺省值一致。

from django.db import migrations


def backfill_enable_sandbox_false(apps, schema_editor):
    """存量任务回填 enable_sandbox=False（与模型 default 对齐，幂等）。"""
    ResearchTask = apps.get_model("research", "ResearchTask")
    ResearchTask.objects.filter(enable_sandbox__isnull=True).update(enable_sandbox=False)


class Migration(migrations.Migration):

    dependencies = [
        ("research", "0017_researchtask_enable_sandbox"),
    ]

    operations = [
        migrations.RunPython(backfill_enable_sandbox_false, migrations.RunPython.noop),
    ]
