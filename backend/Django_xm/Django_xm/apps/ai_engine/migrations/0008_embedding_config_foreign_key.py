"""Embedding 配置扩展：OneToOne → ForeignKey，新增 name/default_model 字段

让一个 Provider 可挂多个 Embedding 配置（Ollama 可同时支持 bge-m3、Qwen3-VL-Embedding 等）

数据迁移：
1. DB schema 层面：删除 provider_id 上的 unique 约束
2. 新增 name、default_model 字段
3. 给已有 4 条记录补 name='bge-m3/text-embedding-3-small/...'、default_model=对应模型名
"""
from django.db import migrations, models
import django.db.models.deletion


# 已有 4 条 Embedding 配置对应的 fallback 默认值（来自 0003/0006 migration）
_DEFAULT_VALUES = {
    "openai": "text-embedding-3-small",
    "ollama": "bge-m3",
    "baidu_qianfan": "embedding-v1",
    "local": "BAAI/bge-small-zh-v1.5",
}


def backfill_name_and_default_model(apps, schema_editor):
    """给已有 EmbeddingProviderConfig 补 name 和 default_model 字段

    注：和 0006 一样，必须走 SQL 而非 apps.get_model，避免 state 漂移问题
    """
    with schema_editor.connection.cursor() as cursor:
        # 1. 找出所有 embedding 配置 + 关联 provider 的 provider_id
        cursor.execute(
            '''
            SELECT ep.id, p.provider_id
            FROM ai_engine_embedding_provider ep
            JOIN ai_engine_provider p ON p.id = ep.provider_id
            '''
        )
        for emb_pk, pid in cursor.fetchall():
            default_model = _DEFAULT_VALUES.get(pid, "")
            name = default_model or "default"
            cursor.execute(
                '''
                UPDATE ai_engine_embedding_provider SET
                    name = %s,
                    default_model = %s
                WHERE id = %s
                ''',
                [name, default_model, emb_pk],
            )


def rollback_name_and_default_model(apps, schema_editor):
    """回滚时把新字段重置为占位值"""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            '''
            UPDATE ai_engine_embedding_provider SET
                name = 'legacy',
                default_model = ''
            '''
        )


def noop(apps, schema_editor):
    """仅同步 Django state"""
    pass


def drop_unique_and_add_columns(apps, schema_editor):
    """前置 SQL：删除 unique 约束（OneToOne -> ForeignKey 关键步骤）

    0004 创建了 unique 约束 ai_engine_embedding_provider_provider_id_new_uniq，
    阻止同一 Provider 挂多个 Embedding。这里删除它以支持 ForeignKey。
    """
    with schema_editor.connection.cursor() as cursor:
        # unique 约束（不是普通索引），用 DROP CONSTRAINT
        cursor.execute(
            "ALTER TABLE ai_engine_embedding_provider "
            "DROP CONSTRAINT IF EXISTS ai_engine_embedding_provider_provider_id_new_uniq"
        )
        # 兼容其他可能的索引名
        cursor.execute(
            "DROP INDEX IF EXISTS ai_engine_embedding_provider_provider_id_key"
        )


def restore_unique_constraint(apps, schema_editor):
    """回滚时恢复 unique 约束"""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ai_engine_embedding_provider_provider_id_new_uniq "
            "ON ai_engine_embedding_provider (provider_id)"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("ai_engine", "0007_remove_embeddingproviderconfig_key_attr_and_more"),
    ]

    operations = [
        # 注：原 0008 的 SeparateDatabaseAndState（删除 key_attr/label/provider_id）已移除，
        # 因为 0004 现在用 SeparateDatabaseAndState 正确同步了 state（删除旧字段 + 添加 provider）。
        # 此处重复删除会导致 FieldDoesNotExist 错误。
        # 2. SQL 层：先删除 unique 索引（让后续 AddField 不冲突）
        migrations.RunPython(drop_unique_and_add_columns, restore_unique_constraint),
        migrations.AddField(
            model_name="embeddingproviderconfig",
            name="name",
            field=models.CharField(
                default="",
                help_text="便于在 Admin 列表中识别，如 'Ollama - bge-m3'、'Ollama - Qwen3-VL-Embedding'",
                max_length=100,
                verbose_name="配置名称",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="embeddingproviderconfig",
            name="default_model",
            field=models.CharField(
                default="",
                help_text="Ollama 用 'bge-m3'；OpenAI 用 'text-embedding-3-small'；多模态用 'MedAIBase/Qwen3-VL-Embedding:2b'",
                max_length=200,
                verbose_name="默认模型名",
            ),
            preserve_default=False,
        ),
        # 2. OneToOne → ForeignKey（Django state 同步）
        migrations.AlterField(
            model_name="embeddingproviderconfig",
            name="provider",
            field=models.ForeignKey(
                help_text="Embedding 与 LLM 属于同一个 Provider，如 OpenAI 既提供 LLM 也提供 Embedding",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="embedding_configs",
                to="ai_engine.aiprovider",
                verbose_name="所属 LLM 提供商",
            ),
        ),
        # 3. 同步 Django state 中 Meta.related_name（实际 DB 不变）
        migrations.AlterModelOptions(
            name="embeddingproviderconfig",
            options={
                "db_table": "ai_engine_embedding_provider",
                "ordering": ["sort_order", "id"],
                "verbose_name": "Embedding 配置",
                "verbose_name_plural": "Embedding 配置",
            },
        ),
        # 4. 给已有记录补 name 和 default_model
        migrations.RunPython(backfill_name_and_default_model, rollback_name_and_default_model),
    ]
