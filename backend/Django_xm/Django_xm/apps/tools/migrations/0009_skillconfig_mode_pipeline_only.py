"""Task 10：SkillConfig.mode 收敛为仅 pipeline

旧 advisor/hybrid 模式随全量注入路径删除（SkillSpec.mode 已移除）：
- SKILL.md body 不再进 system prompt，改由模型经 activate_skill 工具按需读取；
- 存量 advisor/hybrid 记录归一化为 pipeline（hybrid 的步骤链执行语义与
  pipeline 一致，仅失去 {skill_instructions} 占位符注入）。

choices 收敛为 AlterField（无 schema 变更），RunPython 归一化存量数据；
反向迁移无法还原原值，为 no-op。
"""

from django.db import migrations, models


def normalize_legacy_modes(apps, schema_editor):
    SkillConfig = apps.get_model("tools", "SkillConfig")
    SkillConfig.objects.filter(mode__in=["advisor", "hybrid"]).update(mode="pipeline")


class Migration(migrations.Migration):

    dependencies = [
        ("tools", "0008_remove_skillpackage_allowed_tools"),
    ]

    operations = [
        migrations.AlterField(
            model_name="skillconfig",
            name="mode",
            field=models.CharField(
                choices=[("pipeline", "管线模式")],
                default="pipeline",
                max_length=20,
                verbose_name="执行模式",
            ),
        ),
        migrations.RunPython(normalize_legacy_modes, migrations.RunPython.noop),
    ]
