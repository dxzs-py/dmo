"""dj-13：DocumentIndex 与 IndexMetadata 双模型合并（第 2/3 步：数据回填）。

本迁移只含 DML（RunPython），结构准备在 0005、FK 切换在 0007，
三者各自独立事务（原因见 0005 docstring）。

回填规则：
- 全名规则：user_{user_id}_{index_name}（user 为空时 index_name 原样）
- 已存在同名 IndexMetadata（向量库元数据行）：补 user 归属/描述，
  DocumentIndex 为软删时对齐墓碑态（保持列表不可见语义）
- 不存在：补建空实体行，继承 DocumentIndex 的描述与软删状态
- Document 逐行回填 index_metadata_id
"""

from django.db import migrations


def merge_document_index_into_index_metadata(apps, schema_editor):
    """DocumentIndex 行合并进 IndexMetadata，Document 外键改指。"""
    DocumentIndex = apps.get_model("knowledge", "DocumentIndex")
    Document = apps.get_model("knowledge", "Document")
    IndexMetadata = apps.get_model("knowledge", "IndexMetadata")

    for di in DocumentIndex._default_manager.all():
        user_id = di.user_id
        full_name = f"user_{user_id}_{di.index_name}" if user_id else di.index_name

        meta = IndexMetadata._default_manager.filter(name=full_name).first()
        if meta is None:
            meta = IndexMetadata(
                name=full_name,
                user_id=user_id,
                description=di.description,
                status="empty",
                num_documents=0,
                is_deleted=di.is_deleted,
                deleted_at=di.deleted_at,
                created_at=di.created_at,
                updated_at=di.updated_at,
            )
            meta.save(using=schema_editor.connection.alias)
        else:
            changed = False
            if meta.user_id is None and user_id:
                meta.user_id = user_id
                changed = True
            if not meta.description and di.description:
                meta.description = di.description
                changed = True
            if di.is_deleted and not meta.is_deleted:
                meta.is_deleted = True
                meta.deleted_at = di.deleted_at
                changed = True
            if changed:
                meta.save(using=schema_editor.connection.alias)

        Document._default_manager.filter(index_id=di.pk).update(index_metadata_id=meta.pk)


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0005_merge_document_index"),
    ]

    operations = [
        migrations.RunPython(merge_document_index_into_index_metadata, migrations.RunPython.noop),
    ]
