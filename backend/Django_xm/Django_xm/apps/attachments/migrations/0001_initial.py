from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('chat', '0006_attachmentcleanuplog_created_at_and_more'),
    ]

    state_operations = [
        migrations.CreateModel(
            name='ChatAttachment',
            fields=[],
            options={
                'db_table': 'chat_attachment',
                'verbose_name': '聊天附件',
                'verbose_name_plural': '聊天附件',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='AttachmentCleanupLog',
            fields=[],
            options={
                'db_table': 'chat_attachment_cleanup_log',
                'verbose_name': '附件清理日志',
                'verbose_name_plural': '附件清理日志',
                'ordering': ['-started_at'],
            },
        ),
        migrations.CreateModel(
            name='StorageAlert',
            fields=[],
            options={
                'db_table': 'chat_storage_alert',
                'verbose_name': '存储空间告警',
                'verbose_name_plural': '存储空间告警',
                'ordering': ['-created_at'],
            },
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=state_operations,
            database_operations=[],
        )
    ]
