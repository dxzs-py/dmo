"""dj-13：DocumentIndex 与 IndexMetadata 双模型合并（第 3/3 步：FK 切换）。

结构已在 0005 准备、数据已在 0006 回填（Document.index_metadata 指向
IndexMetadata），本迁移在同一独立事务内完成结构收尾：
1. 删除 Document.index 旧 FK（指向 DocumentIndex）
2. 临时 FK index_metadata 改名 index 并置非空
3. 删除 DocumentIndex 模型
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0006_backfill_document_index"),
    ]

    operations = [
        # 1. 旧 FK 删除
        migrations.RemoveField(
            model_name="document",
            name="index",
        ),
        # 2. 临时 FK 转正
        migrations.RenameField(
            model_name="document",
            old_name="index_metadata",
            new_name="index",
        ),
        migrations.AlterField(
            model_name="document",
            name="index",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="knowledge.indexmetadata",
                verbose_name="所属索引",
            ),
        ),
        # 3. 删除 DocumentIndex 实体
        migrations.DeleteModel(
            name="DocumentIndex",
        ),
    ]
