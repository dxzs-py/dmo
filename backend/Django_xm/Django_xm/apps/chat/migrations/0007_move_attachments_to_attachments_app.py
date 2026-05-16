from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0006_attachmentcleanuplog_created_at_and_more'),
        ('attachments', '0001_initial'),
    ]

    state_operations = [
        migrations.DeleteModel(
            name='ChatAttachment',
        ),
        migrations.DeleteModel(
            name='AttachmentCleanupLog',
        ),
        migrations.DeleteModel(
            name='StorageAlert',
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=state_operations,
            database_operations=[],
        )
    ]
