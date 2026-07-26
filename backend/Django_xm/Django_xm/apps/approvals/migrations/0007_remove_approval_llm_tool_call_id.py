from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('approvals', '0006_alter_approval_source'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='approval',
            name='llm_tool_call_id',
        ),
    ]
