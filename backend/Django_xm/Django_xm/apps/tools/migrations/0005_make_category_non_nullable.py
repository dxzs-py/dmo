import django.db.models.deletion
from django.db import migrations, models


def ensure_category_populated(apps, schema_editor):
    ToolCategory = apps.get_model('tools', 'ToolCategory')
    try:
        general_cat = ToolCategory.objects.get(code='general')
    except ToolCategory.DoesNotExist:
        general_cat = ToolCategory.objects.first()
    if general_cat is None:
        return

    for model_name in ('CustomTool', 'McpServerConfig', 'SkillConfig', 'SkillPackage'):
        Model = apps.get_model('tools', model_name)
        Model.objects.filter(category__isnull=True).update(category=general_cat.pk)


class Migration(migrations.Migration):

    dependencies = [
        ("tools", "0004_populate_tool_classification_data"),
    ]

    operations = [
        migrations.RunPython(ensure_category_populated, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customtool",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)s_set",
                to="tools.toolcategory",
                verbose_name="功能分类",
            ),
        ),
        migrations.AlterField(
            model_name="mcpserverconfig",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)s_set",
                to="tools.toolcategory",
                verbose_name="功能分类",
            ),
        ),
        migrations.AlterField(
            model_name="skillconfig",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)s_set",
                to="tools.toolcategory",
                verbose_name="功能分类",
            ),
        ),
        migrations.AlterField(
            model_name="skillpackage",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="%(class)s_set",
                to="tools.toolcategory",
                verbose_name="功能分类",
            ),
        ),
    ]
