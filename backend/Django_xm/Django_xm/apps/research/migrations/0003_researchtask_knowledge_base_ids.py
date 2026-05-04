from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("research", "0002_researchtask_cost_researchtask_model_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="researchtask",
            name="knowledge_base_ids",
            field=models.JSONField(
                default=list,
                blank=True,
                verbose_name="关联知识库ID列表",
            ),
        ),
    ]
