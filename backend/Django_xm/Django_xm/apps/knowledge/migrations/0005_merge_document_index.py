"""dj-13：DocumentIndex 与 IndexMetadata 双模型合并（第 1/3 步：结构准备）。

本迁移只含 DDL，数据回填在 0006、FK 切换在 0007，三者各自独立事务。

拆分原因：AddField(db_index=True) 会把 CREATE INDEX 放入 schema_editor 的
deferred_sql（迁移事务结束时执行），若同一迁移内 RunPython 对同一表做 DML，
DEFERRABLE INITIALLY DEFERRED FK（Django PG 后端默认）的延迟检查事件未结算，
事务结束时执行 deferred CREATE INDEX 会报 "cannot CREATE INDEX ... because
it has pending trigger events"。DDL 与 DML 拆分为独立事务提交后各自结算。

结构变更：
1. IndexMetadata 继承 BaseModel：加软删字段（is_deleted 带索引 / deleted_at）
2. created_at 加 db_index，num_documents 语义修正（向量块数镜像）
3. Document 加临时 FK index_metadata（null=True，回填后在 0007 收紧）
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0004_remove_indexmetadata_idx_name"),
    ]

    operations = [
        # 1. IndexMetadata 继承 BaseModel：加软删字段 + created_at 加索引
        migrations.AddField(
            model_name="indexmetadata",
            name="is_deleted",
            field=models.BooleanField(default=False, db_index=True, verbose_name="是否已删除"),
        ),
        migrations.AddField(
            model_name="indexmetadata",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="删除时间"),
        ),
        migrations.AlterField(
            model_name="indexmetadata",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="创建时间"),
        ),
        # num_documents 语义修正：向量块数镜像（文件数由 Document 表派生，不再存储）
        migrations.AlterField(
            model_name="indexmetadata",
            name="num_documents",
            field=models.IntegerField(default=0, verbose_name="向量块数"),
        ),
        # 2. Document 临时 FK（先可空，0006 回填后 0007 收紧）
        migrations.AddField(
            model_name="document",
            name="index_metadata",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="knowledge.indexmetadata",
                verbose_name="所属索引",
            ),
        ),
    ]
