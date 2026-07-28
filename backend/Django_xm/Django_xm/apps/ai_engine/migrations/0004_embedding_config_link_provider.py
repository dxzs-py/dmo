"""EmbeddingProviderConfig 关联 AIProvider

将 EmbeddingProviderConfig 的 provider_id/label/key_attr 独立字段
改为 OneToOneField 关联 AIProvider，复用 Provider 的身份信息。
"""
import django.db.models.deletion
from django.db import migrations, models


def forward_link(apps, schema_editor):
    """将现有 EmbeddingProviderConfig 关联到 AIProvider"""
    AIProvider = apps.get_model("ai_engine", "AIProvider")
    EmbeddingProviderConfig = apps.get_model("ai_engine", "EmbeddingProviderConfig")

    # 构建 provider_id → AIProvider pk 映射
    provider_map = {p.provider_id: p.pk for p in AIProvider.objects.all()}

    # 对于 Embedding 的 provider_id 在 AIProvider 中不存在的情况，创建 AIProvider
    emb_data = list(EmbeddingProviderConfig.objects.all().values('id', 'provider_id', 'label', 'key_attr', 'is_enabled'))
    for row in emb_data:
        pid = row['provider_id']
        if pid not in provider_map:
            # 创建一个仅 Embedding 的 AIProvider
            p = AIProvider.objects.create(
                provider_id=pid,
                provider="local",
                label=row['label'].split('(')[0].strip() or pid,
                icon="📦",
                default_model="",
                api_key_env=None,
                api_key_attr=row['key_attr'] or None,
                base_url_attr=None,
                model_attr=None,
                special_params={},
                presets={},
                is_enabled=row['is_enabled'],
                sort_order=90,
            )
            provider_map[pid] = p.pk

    # 用 SQL 更新：添加 provider_id 列，填充值，然后转为 FK
    with schema_editor.connection.cursor() as cursor:
        # 添加临时 provider_id 列
        cursor.execute("ALTER TABLE ai_engine_embedding_provider ADD COLUMN provider_id_new bigint")
        # 填充值
        for row in emb_data:
            pk = provider_map.get(row['provider_id'])
            if pk:
                cursor.execute(
                    "UPDATE ai_engine_embedding_provider SET provider_id_new = %s WHERE id = %s",
                    [pk, row['id']]
                )
        # 设为 NOT NULL
        cursor.execute("ALTER TABLE ai_engine_embedding_provider ALTER COLUMN provider_id_new SET NOT NULL")
        # 添加唯一约束
        cursor.execute("ALTER TABLE ai_engine_embedding_provider ADD CONSTRAINT ai_engine_embedding_provider_provider_id_new_uniq UNIQUE (provider_id_new)")
        # 删除旧字段
        cursor.execute("ALTER TABLE ai_engine_embedding_provider DROP COLUMN provider_id")
        cursor.execute("ALTER TABLE ai_engine_embedding_provider DROP COLUMN label")
        cursor.execute("ALTER TABLE ai_engine_embedding_provider DROP COLUMN key_attr")
        # 重命名
        cursor.execute("ALTER TABLE ai_engine_embedding_provider RENAME COLUMN provider_id_new TO provider_id")
        # 添加 FK 约束
        cursor.execute(
            "ALTER TABLE ai_engine_embedding_provider ADD CONSTRAINT ai_engine_embedding_provider_provider_fk "
            "FOREIGN KEY (provider_id) REFERENCES ai_engine_provider(id) ON DELETE CASCADE"
        )


def reverse_unlink(apps, schema_editor):
    """回滚：恢复 EmbeddingProviderConfig 的独立字段"""
    AIProvider = apps.get_model("ai_engine", "AIProvider")
    EmbeddingProviderConfig = apps.get_model("ai_engine", "EmbeddingProviderConfig")

    with schema_editor.connection.cursor() as cursor:
        # 添加旧字段
        cursor.execute("ALTER TABLE ai_engine_embedding_provider ADD COLUMN provider_id_old varchar(50)")
        cursor.execute("ALTER TABLE ai_engine_embedding_provider ADD COLUMN label_old varchar(100)")
        cursor.execute("ALTER TABLE ai_engine_embedding_provider ADD COLUMN key_attr_old varchar(100)")

        # 从 AIProvider 填充
        for emb in EmbeddingProviderConfig.objects.all():
            p = AIProvider.objects.get(pk=emb.provider_id)
            cursor.execute(
                "UPDATE ai_engine_embedding_provider SET provider_id_old = %s, label_old = %s, key_attr_old = %s WHERE id = %s",
                [p.provider_id, p.label, p.api_key_attr or '', emb.id]
            )

        # 删除 FK 和 provider_id
        cursor.execute("ALTER TABLE ai_engine_embedding_provider DROP CONSTRAINT IF EXISTS ai_engine_embedding_provider_provider_fk")
        cursor.execute("ALTER TABLE ai_engine_embedding_provider DROP CONSTRAINT IF EXISTS ai_engine_embedding_provider_provider_id_new_uniq")
        cursor.execute("ALTER TABLE ai_engine_embedding_provider DROP COLUMN provider_id")

        # 重命名
        cursor.execute("ALTER TABLE ai_engine_embedding_provider RENAME COLUMN provider_id_old TO provider_id")
        cursor.execute("ALTER TABLE ai_engine_embedding_provider RENAME COLUMN label_old TO label")
        cursor.execute("ALTER TABLE ai_engine_embedding_provider RENAME COLUMN key_attr_old TO key_attr")

        # 恢复约束
        cursor.execute("ALTER TABLE ai_engine_embedding_provider ALTER COLUMN provider_id SET NOT NULL")
        cursor.execute("CREATE UNIQUE INDEX ai_engine_embedding_provider_provider_id_key ON ai_engine_embedding_provider (provider_id)")


class Migration(migrations.Migration):

    dependencies = [
        ("ai_engine", "0003_seed_registry_data"),
    ]

    operations = [
        # 用 RunPython 执行 SQL 迁移，因为 Django ORM 的 AddField + RemoveField 在有数据时容易出问题
        migrations.RunPython(forward_link, reverse_unlink),

        # 更新 Django state：告诉 Django 现在的模型结构
        migrations.AlterModelOptions(
            name='embeddingproviderconfig',
            options={
                'db_table': 'ai_engine_embedding_provider',
                'ordering': ['sort_order'],
                'verbose_name': 'Embedding 配置',
                'verbose_name_plural': 'Embedding 配置',
            },
        ),
        # 同步 Django state：实际 DB schema 在 forward_link 中已通过 SQL 创建了 provider_id FK 列
        # 并删除了 provider_id/label/key_attr 独立字段。
        # 注：不能用 AlterField(name='provider')，因为 0002_initial 中 EmbeddingProviderConfig
        # 没有 'provider' 字段（只有 'provider_id' CharField），AlterField 会抛
        # FieldDoesNotExist。用 SeparateDatabaseAndState 只更新 state（DB 已在 forward_link 处理）：
        #   1. 删除 state 中残留的 provider_id/label/key_attr（0002 创建的 CharField）
        #   2. 添加 provider OneToOneField（与 DB 中 forward_link 创建的 FK 列对齐）
        migrations.SeparateDatabaseAndState(
            database_operations=[],  # DB 已在 forward_link 中通过 SQL 处理
            state_operations=[
                migrations.RemoveField(
                    model_name='embeddingproviderconfig',
                    name='provider_id',
                ),
                migrations.RemoveField(
                    model_name='embeddingproviderconfig',
                    name='label',
                ),
                migrations.RemoveField(
                    model_name='embeddingproviderconfig',
                    name='key_attr',
                ),
                migrations.AddField(
                    model_name='embeddingproviderconfig',
                    name='provider',
                    field=models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='embedding_config',
                        to='ai_engine.aiprovider',
                    ),
                ),
            ],
        ),
    ]
