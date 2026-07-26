"""合并 selected_knowledge_base 到 selected_knowledge_bases

将 selected_knowledge_base（单选 CharField）的值合并到
selected_knowledge_bases（多选 JSONField），为后续移除单选字段做准备。
"""

from django.db import migrations


def merge_knowledge_base_fields(apps, schema_editor):
    ChatSession = apps.get_model('chat', 'ChatSession')
    for session in ChatSession.objects.filter(selected_knowledge_base__isnull=False).exclude(selected_knowledge_base=''):
        # 仅当 selected_knowledge_bases 为空时才合并
        if not session.selected_knowledge_bases:
            session.selected_knowledge_bases = [session.selected_knowledge_base]
            session.save(update_fields=['selected_knowledge_bases'])


def reverse_merge(apps, schema_editor):
    # 反向操作不需要，保留原字段值
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('chat', '0005_add_approval_field'),
    ]

    operations = [
        migrations.RunPython(merge_knowledge_base_fields, reverse_merge),
    ]
