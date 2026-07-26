# Generated for Task 8.3 - backfill ChatMessage.versions for assistant messages

from django.db import migrations


def backfill_assistant_versions(apps, schema_editor):
    """为 role='assistant' 且 versions 为空的 ChatMessage 回填单元素 versions 数组。

    将当前 content/tool_calls/sources/reasoning/model 包装为 versions 的唯一元素，
    current_version 置为 0。user/system 消息不处理（versions 保持空数组）。
    """
    ChatMessage = apps.get_model('chat', 'ChatMessage')
    queryset = ChatMessage.objects.filter(role='assistant', versions=[])
    to_update = []
    for message in queryset.iterator():
        message.versions = [{
            "content": message.content,
            "tool_calls": message.tool_calls or [],
            "sources": message.sources or [],
            "reasoning": message.reasoning or "",
            "created_at": message.created_at.isoformat() if message.created_at else "",
            "model": message.model or "",
        }]
        message.current_version = 0
        to_update.append(message)

    if to_update:
        ChatMessage.objects.bulk_update(
            to_update,
            ['versions', 'current_version'],
            batch_size=500,
        )


def reverse_backfill_assistant_versions(apps, schema_editor):
    """回滚：将 assistant 消息的 versions 清空为 []，current_version 重置为 0。

    仅清空由正向迁移写入的单元素 versions，不恢复原始字段（content 等保持现状，
    因为正向迁移未修改这些字段）。
    """
    ChatMessage = apps.get_model('chat', 'ChatMessage')
    queryset = ChatMessage.objects.filter(role='assistant')
    to_update = []
    for message in queryset.iterator():
        message.versions = []
        message.current_version = 0
        to_update.append(message)

    if to_update:
        ChatMessage.objects.bulk_update(
            to_update,
            ['versions', 'current_version'],
            batch_size=500,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0011_alter_chatmessage_current_version'),
    ]

    operations = [
        migrations.RunPython(
            backfill_assistant_versions,
            reverse_code=reverse_backfill_assistant_versions,
        ),
    ]
