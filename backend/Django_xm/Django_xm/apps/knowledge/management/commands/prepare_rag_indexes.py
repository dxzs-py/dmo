"""RAG 索引准备命令（O6/O7）。

创建两个索引（均 CONCURRENTLY IF NOT EXISTS，幂等且不阻塞写入）：

1. ``HNSW``（vector_cosine_ops）：让 pgvector 相似度检索从顺序扫描转为 ANN；
2. ``pg_trgm GIN``（document gin_trgm_ops）：让 Hybrid 的 2-gram ILIKE 走索引。

用法（backend/Django_xm 目录）：

    # 建索引（默认两个都建）
    python manage.py prepare_rag_indexes

    # 只看状态
    python manage.py prepare_rag_indexes --status

    # 只建 HNSW（例如无 pg_trgm 权限时）
    python manage.py prepare_rag_indexes --skip-trigram

    注意：CREATE INDEX CONCURRENTLY 不能在事务块内，本命令依赖 autocommit 连接；
    建索引期间会有一次全表扫描，建议在低峰期执行。
"""

from django.core.management.base import BaseCommand
from django.db import connections

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def _index_exists(cursor, index_name: str) -> bool:
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = %s)", [index_name]
    )
    return bool(cursor.fetchone()[0])


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.columns"
        "  WHERE table_name = %s AND column_name = %s"
        ")",
        [table, column],
    )
    return bool(cursor.fetchone()[0])


def _index_size(cursor, index_name: str) -> str:
    cursor.execute("SELECT pg_size_pretty(pg_relation_size(%s))", [index_name])
    row = cursor.fetchone()
    return row[0] if row else "unknown"


class Command(BaseCommand):
    help = "为 RAG 创建 HNSW（向量）索引，幂等且不阻塞写入"

    def add_arguments(self, parser):
        parser.add_argument("--status", action="store_true", help="只输出索引状态，不创建")
        parser.add_argument("--skip-hnsw", action="store_true", help="跳过 HNSW 索引")

    def handle(self, *args, **options):
        from Django_xm.apps.knowledge.vector_store.pgvector_backend import (
            get_embedding_table_name,
            quote_identifier,
        )

        table = get_embedding_table_name()
        quoted_table = quote_identifier(table)
        hnsw_index = f"{table}_embedding_hnsw_idx"[:63]

        with connections["default"].cursor() as cursor:
            if not _column_exists(cursor, table, "embedding"):
                self.stderr.write(f"表 {table} 不存在 embedding 列，跳过")
                return

            hnsw_exists = _index_exists(cursor, hnsw_index)

            self.stdout.write(f"embedding 表: {table}")
            self.stdout.write(
                f"  HNSW      : {'存在' if hnsw_exists else '缺失'}"
                + (f"（{_index_size(cursor, hnsw_index)}）" if hnsw_exists else "")
            )

            if options["status"]:
                return

            if not options["skip_hnsw"] and not hnsw_exists:
                self._create_hnsw(cursor, quoted_table, hnsw_index)

    def _create_hnsw(self, cursor, quoted_table: str, index_name: str) -> None:
        from Django_xm.apps.knowledge.vector_store.pgvector_backend import quote_identifier

        quoted_index = quote_identifier(index_name)
        sql = (
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {quoted_index} "
            f"ON {quoted_table} USING hnsw (embedding vector_cosine_ops)"
        )
        try:
            self.stdout.write("  正在创建 HNSW 索引（CONCURRENTLY）...")
            cursor.execute(sql)
            self.stdout.write(self.style.SUCCESS(f"  HNSW 索引已创建: {index_name}"))
        except Exception as e:
            self.stderr.write(
                "  HNSW 索引创建失败（可能缺少 vector 扩展或权限）："
                f"{e}\n  可手动执行：{sql}"
            )
