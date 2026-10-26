"""``prepare_rag_indexes`` 命令回归（Phase 3：只建 HNSW）。

Phase 3：sparse 通道统一应用层 BM25 后，pg_trgm GIN 索引不再需要，命令只建 HNSW。

锁定：
1. 默认创建 HNSW（vector_cosine_ops）；
2. ``--status`` 只报告不创建；
3. 索引已存在时不重复执行 DDL（幂等）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_prepare_rag_indexes --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.knowledge.management.commands.prepare_rag_indexes import Command

_PGVECTOR = "Django_xm.apps.knowledge.vector_store.pgvector_backend"
_COMMAND = "Django_xm.apps.knowledge.management.commands.prepare_rag_indexes"


class PrepareRagIndexesCommandTests(unittest.TestCase):
    def _run(self, fetch_results, status=False, skip_hnsw=False):
        cursor = mock.MagicMock(name="cursor")
        cursor.fetchone.side_effect = list(fetch_results)
        cm = mock.MagicMock(name="cursor_cm")
        cm.__enter__.return_value = cursor
        cm.__exit__.return_value = False

        fake_conn = mock.MagicMock(name="connection")
        fake_conn.cursor.return_value = cm

        with (
            mock.patch(f"{_PGVECTOR}.get_embedding_table_name", return_value="langchain_pg_embedding"),
            mock.patch(f"{_COMMAND}.connections", {"default": fake_conn}),
        ):
            Command().handle(status=status, skip_hnsw=skip_hnsw)
        return [call.args[0] for call in cursor.execute.call_args_list if call.args]

    def test_creates_hnsw_index(self):
        # 顺序：embedding 列存在 / hnsw 索引缺失
        statements = self._run([(True,), (False,)])

        self.assertTrue(
            any("USING hnsw (embedding vector_cosine_ops)" in sql for sql in statements)
        )
        self.assertFalse(any("gin_trgm_ops" in sql for sql in statements))

    def test_status_only_reports(self):
        statements = self._run([(True,), (False,)], status=True)

        self.assertFalse(any("CREATE INDEX" in sql for sql in statements))

    def test_existing_index_not_recreated(self):
        # 列存在 / 索引存在 / 索引大小
        statements = self._run([(True,), (True,), ("0 MB",)])

        self.assertFalse(any("CREATE INDEX" in sql for sql in statements))

    def test_missing_embedding_column_skips(self):
        statements = self._run([(False,)])

        self.assertFalse(any("CREATE INDEX" in sql for sql in statements))


if __name__ == "__main__":
    unittest.main()
