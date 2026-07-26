import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Approval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('interrupt_id', models.CharField(db_index=True, max_length=128, unique=True)),
                ('source', models.CharField(choices=[('chat', 'Chat'), ('deep_research', 'Deep Research')], max_length=32)),
                ('source_id', models.CharField(db_index=True, max_length=128)),
                ('chat_session_id', models.CharField(blank=True, db_index=True, max_length=128, null=True)),
                ('tool_name', models.CharField(max_length=128)),
                ('title', models.CharField(default='', max_length=256)),
                ('description', models.TextField(default='')),
                ('action', models.CharField(default='confirm', max_length=32)),
                ('operation', models.CharField(blank=True, default='', max_length=256)),
                ('danger_level', models.CharField(default='medium', max_length=32)),
                ('parameters', models.JSONField(blank=True, default=dict)),
                ('llm_tool_call_id', models.CharField(blank=True, default='', max_length=128)),
                ('state', models.CharField(choices=[('pending', 'Pending'), ('processing', 'Processing'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('timeout', 'Timeout')], default='pending', max_length=32)),
                ('user_input', models.TextField(blank=True, null=True)),
                ('extra', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['source', 'source_id'], name='approvals_source_source_id_idx'),
                    models.Index(fields=['chat_session_id', 'state'], name='approvals_chat_session_state_idx'),
                    models.Index(fields=['state', 'created_at'], name='approvals_state_created_at_idx'),
                ],
            },
        ),
    ]
