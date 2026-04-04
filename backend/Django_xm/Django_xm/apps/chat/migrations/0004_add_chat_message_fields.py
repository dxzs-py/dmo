# Generated manually to add missing fields to chat_message table

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0003_add_mode_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmessage',
            name='sources',
            field=models.JSONField(blank=True, default=list, verbose_name='来源'),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='plan',
            field=models.JSONField(blank=True, default=dict, null=True, verbose_name='计划'),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='chain_of_thought',
            field=models.JSONField(blank=True, default=dict, null=True, verbose_name='思维链'),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='tool_calls',
            field=models.JSONField(blank=True, default=list, verbose_name='工具调用'),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='reasoning',
            field=models.JSONField(blank=True, default=dict, null=True, verbose_name='推理'),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='versions',
            field=models.JSONField(blank=True, default=list, verbose_name='消息版本'),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='current_version',
            field=models.IntegerField(default=0, verbose_name='当前版本索引'),
        ),
    ]
