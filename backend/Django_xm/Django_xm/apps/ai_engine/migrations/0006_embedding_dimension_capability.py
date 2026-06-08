"""Embedding 维度能力：为 EmbeddingProviderConfig 增加 MRL 截断相关字段

- native_max_dimension: 原生最大维度（无 dimensions 参数时的输出）
- min_dimension: MRL 截断下限
- supported_dimensions: 支持的维度列表，空列表表示固定维度

并为已有 4 个 embedding provider 补默认值：
- openai (text-embedding-3-small): 1536 / 0 / []  (固定维度，不开放截断)
- ollama (bge-m3): 1024 / 0 / []  (固定维度)
- baidu_qianfan: 1536 / 0 / []  (固定维度)
- local (bge-small-zh-v1.5): 512 / 0 / []  (固定维度)
"""
from django.db import migrations, models


# 已有 provider 的默认值（provider_id 是 EmbeddingProviderConfig 通过 provider 关联的 AIProvider.provider_id）
_DEFAULT_VALUES = {
    # provider_id: (native_max, min, supported)
    "openai": (1536, 0, []),
    "ollama": (1024, 0, []),
    "baidu_qianfan": (1536, 0, []),
    "local": (512, 0, []),
}


def backfill_dimension_capability(apps, schema_editor):
    """为已有 EmbeddingProviderConfig 补 MRL 截断相关字段默认值

    注意：使用 SQL 直接操作而非 apps.get_model().objects.update()，因为：
    - 0004 把 EmbeddingProviderConfig 从 5 字段（provider_id/label/key_attr/...）
      改为 1 个 provider FK 字段，但 migration state 没同步
    - 通过 apps.get_model 拿到的"历史 state 模型"会与实际 DB schema 不一致
    - 直接走 SQL 完全避免 state/DB drift
    """
    with schema_editor.connection.cursor() as cursor:
        # 提取 provider_id -> provider 表的映射
        cursor.execute('SELECT id, provider_id FROM ai_engine_provider')
        provider_id_to_pk = {pid: pk for pk, pid in cursor.fetchall()}

        # 查找每个 Embedding 的 provider_id (FK 列)
        cursor.execute('SELECT id, provider_id FROM ai_engine_embedding_provider')
        for emb_pk, provider_fk in cursor.fetchall():
            # 查 provider_id 字符串
            cursor.execute(
                'SELECT provider_id FROM ai_engine_provider WHERE id = %s',
                [provider_fk],
            )
            row = cursor.fetchone()
            if not row:
                continue
            pid = row[0]
            if pid not in _DEFAULT_VALUES:
                continue
            native_max, min_dim, supp = _DEFAULT_VALUES[pid]
            # 导入支持：psycopg2 接受 list/dict 直接序列化；'[]' 也可直接
            import json as _json
            cursor.execute(
                '''
                UPDATE ai_engine_embedding_provider SET
                    native_max_dimension = %s,
                    min_dimension = %s,
                    supported_dimensions = %s
                WHERE id = %s
                ''',
                [native_max, min_dim, _json.dumps(supp), emb_pk],
            )


def rollback_dimension_capability(apps, schema_editor):
    """回滚时把 3 个新字段置 0/[]，保留 dimension 字段"""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            '''
            UPDATE ai_engine_embedding_provider SET
                native_max_dimension = 0,
                min_dimension = 0,
                supported_dimensions = '[]'::jsonb
            '''
        )


class Migration(migrations.Migration):

    dependencies = [
        ("ai_engine", "0005_thinking_mode_unified"),
    ]

    operations = [
        migrations.AddField(
            model_name="embeddingproviderconfig",
            name="native_max_dimension",
            field=models.IntegerField(
                default=0,
                help_text="模型不传 dimensions 时的输出维度（如 nomic-embed-text 768、bge-m3 1024、Qwen3-VL-Embedding-2B 2048）。≤ 0 表示未设置。",
                verbose_name="原生最大维度",
            ),
        ),
        migrations.AddField(
            model_name="embeddingproviderconfig",
            name="min_dimension",
            field=models.IntegerField(
                default=0,
                help_text="MRL 截断下限，0 表示不支持截断（如 bge-m3 填 0；nomic-embed-text 填 64）。",
                verbose_name="最小支持维度",
            ),
        ),
        migrations.AddField(
            model_name="embeddingproviderconfig",
            name="supported_dimensions",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="MRL 模型可显式指定的维度，如 [64, 128, 256, 512, 768]。空列表表示固定维度，dimension 字段只读。",
                verbose_name="支持的维度列表",
            ),
        ),
        migrations.RunPython(backfill_dimension_capability, rollback_dimension_capability),
    ]
