"""pgvector_backend ORM 封装层单元测试（SubTask 20.3）。

测试覆盖：
- ``_quote_identifier()`` 包装表名（防 SQL 注入，Task 20.2 关键修复）
- ``read_all_documents()`` 返回正确格式与空集合处理
- ``add_documents()`` 委托到底层 vector_store（含 ``add_texts`` 回退与不支持异常）
- ``delete`` / ``remove_documents`` / ``remove_documents_by_metadata`` /
  ``remove_documents_by_metadata_like`` 删除操作通过封装层
- ``search`` / ``list_collections`` / ``exists`` / ``get_stats`` 查询操作
- 所有原生 SQL 表名均通过 ``quote_name`` 包装（Task 20.2 回归保护）

所有测试通过 ``unittest.mock.patch`` 模拟 ``connections``，不依赖真实 PostgreSQL。

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.knowledge.vector_store.tests.test_pgvector_backend --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# Django 环境初始化（与项目现有测试一致）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.vector_store.pgvector_backend import (
    PGVectorBackend,
    _quote_identifier,
)

# 被测模块中 connections 的导入路径
_CONNECTIONS_PATH = "Django_xm.apps.knowledge.vector_store.pgvector_backend.connections"


def _pg_quote_name(name: str) -> str:
    """模拟 PostgreSQL backend 的 quote_name：双引号包裹 + 内部双引号转义。"""
    return '"' + name.replace('"', '""') + '"'


def _setup_mock_connections(quote_name_side_effect=_pg_quote_name):
    """构造 mock connections。

    配置：
    - ``connections["default"]`` 返回同一个 mock_default
    - ``mock_default.ops.quote_name`` 使用 PostgreSQL 风格包装
    - ``mock_default.cursor()`` 作为上下文管理器返回同一个 mock_cursor

    Returns:
        (mock_connections, mock_cursor)
    """
    mock_connections = MagicMock()
    mock_default = mock_connections.__getitem__.return_value
    mock_default.ops.quote_name.side_effect = quote_name_side_effect
    mock_cursor = MagicMock()
    mock_default.cursor.return_value.__enter__.return_value = mock_cursor
    return mock_connections, mock_cursor


def _fetchone_tables_exist():
    """返回 helper 函数 fetchone 序列：表名查询 + 两张表存在性检查均通过。"""
    return [
        ("langchain_pg_embedding",),  # _get_embedding_table_name
        ("langchain_pg_collection",),  # _get_collection_table_name
        (True,),  # _check_table_exists(embedding)
        (True,),  # _check_table_exists(collection)
    ]


class QuoteIdentifierTest(unittest.TestCase):
    """测试 ``_quote_identifier()`` 包装表名，防 SQL 注入（Task 20.2）。"""

    def test_wraps_table_name_with_quotes(self):
        """普通表名被双引号包装，并委托给 ``quote_name``。"""
        mock_connections, _ = _setup_mock_connections()
        with patch(_CONNECTIONS_PATH, mock_connections):
            result = _quote_identifier("langchain_pg_collection")
        self.assertEqual(result, '"langchain_pg_collection"')
        mock_connections["default"].ops.quote_name.assert_called_once_with("langchain_pg_collection")

    def test_prevents_sql_injection_in_table_name(self):
        """恶意表名（含 ``; DROP TABLE``）被安全包装为单一标识符。

        包装后内部双引号被转义为 ``""``，整体被双引号包裹，
        在 SQL 中只会被解析为一个（非法的）表名，不会拆分为多条语句。
        """
        malicious = 'foo"; DROP TABLE bar; --'
        mock_connections, _ = _setup_mock_connections()
        with patch(_CONNECTIONS_PATH, mock_connections):
            result = _quote_identifier(malicious)
        self.assertEqual(result, '"foo""; DROP TABLE bar; --"')
        # 原样恶意串（末尾带未转义双引号）不应出现在结果中
        self.assertNotIn(malicious + '"', result)


class ReadAllDocumentsTest(unittest.TestCase):
    """测试 ``read_all_documents()`` 返回正确格式。"""

    @patch(_CONNECTIONS_PATH)
    def test_returns_list_of_documents(self, mock_connections):
        """返回 ``Document`` 列表，content/metadata 正确映射。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = [
            ("content1", {"source": "a"}),
            ("content2", {"source": "b"}),
        ]

        backend = PGVectorBackend()
        docs = backend.read_all_documents("my_collection")

        self.assertEqual(len(docs), 2)
        self.assertIsInstance(docs[0], Document)
        self.assertEqual(docs[0].page_content, "content1")
        self.assertEqual(docs[0].metadata, {"source": "a"})
        self.assertEqual(docs[1].page_content, "content2")
        self.assertEqual(docs[1].metadata, {"source": "b"})

    @patch(_CONNECTIONS_PATH)
    def test_handles_empty_collection(self, mock_connections):
        """集合无文档时返回空列表。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = []

        backend = PGVectorBackend()
        docs = backend.read_all_documents("empty_collection")

        self.assertEqual(docs, [])

    @patch(_CONNECTIONS_PATH)
    def test_returns_empty_when_tables_missing(self, mock_connections):
        """表不存在时返回空列表，不执行主查询。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        # embedding 表存在检查返回 False，短路退出
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_embedding",),  # _get_embedding_table_name
            ("langchain_pg_collection",),  # _get_collection_table_name
            (False,),  # _check_table_exists(embedding) -> False
        ]

        backend = PGVectorBackend()
        docs = backend.read_all_documents("my_collection")

        self.assertEqual(docs, [])
        mock_cursor.fetchall.assert_not_called()

    @patch(_CONNECTIONS_PATH)
    def test_skips_rows_with_falsy_content_or_metadata(self, mock_connections):
        """content 或 metadata 为空值的行被跳过。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = [
            ("content1", {"source": "a"}),
            ("", {"source": "b"}),  # content 为空 -> 跳过
            ("content3", None),  # metadata 为 None -> 跳过
            ("content4", {"source": "d"}),
        ]

        backend = PGVectorBackend()
        docs = backend.read_all_documents("my_collection")

        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0].page_content, "content1")
        self.assertEqual(docs[1].page_content, "content4")

    @patch(_CONNECTIONS_PATH)
    def test_normalizes_non_dict_metadata(self, mock_connections):
        """非 dict 的 metadata 被规范化为空 dict。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = [
            ("content1", "not-a-dict"),
        ]

        backend = PGVectorBackend()
        docs = backend.read_all_documents("my_collection")

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].metadata, {})

    @patch(_CONNECTIONS_PATH)
    def test_sql_uses_quoted_table_identifiers(self, mock_connections):
        """主查询 SQL 使用 ``quote_name`` 包装后的表名（Task 20.2 回归保护）。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = []

        backend = PGVectorBackend()
        backend.read_all_documents("my_collection")

        execute_sqls = [c.args[0] for c in mock_cursor.execute.call_args_list if c.args]
        main_query = next(
            (sql for sql in execute_sqls if "SELECT document, cmetadata" in sql),
            None,
        )
        self.assertIsNotNone(main_query, "主查询未执行")
        # 表名应被双引号包装，而非裸表名拼接
        self.assertIn('"langchain_pg_embedding"', main_query)
        self.assertIn('"langchain_pg_collection"', main_query)
        # 不应出现裸表名（未包装）作为 FROM 目标
        self.assertNotIn("FROM langchain_pg_embedding", main_query)


class AddDocumentsTest(unittest.TestCase):
    """测试 ``add_documents()`` 委托到底层 vector_store。

    注意：实际实现签名是 ``add_documents(self, vector_store, documents)``，
    不含 ``ids`` 参数；幂等性（基于内容 hash 的稳定 ID）由上层
    ``index_service.add_documents`` 负责，不在本封装层。
    """

    def test_delegates_to_vector_store_add_documents(self):
        """优先调用 ``vector_store.add_documents``。"""
        mock_vs = MagicMock()
        mock_vs.add_documents.return_value = ["id1", "id2"]
        docs = [Document(page_content="a"), Document(page_content="b")]

        backend = PGVectorBackend()
        ids = backend.add_documents(mock_vs, docs)

        self.assertEqual(ids, ["id1", "id2"])
        mock_vs.add_documents.assert_called_once_with(docs)

    def test_falls_back_to_add_texts(self):
        """当 vector_store 不支持 ``add_documents`` 时回退到 ``add_texts``。"""

        # 使用真实类避免 MagicMock 的 hasattr 永真
        class FakeStore:
            def add_texts(self, texts, metadatas):
                self._texts = texts
                self._metadatas = metadatas
                return ["t1", "t2"]

        fake_vs = FakeStore()
        docs = [
            Document(page_content="a", metadata={"k": 1}),
            Document(page_content="b", metadata={"k": 2}),
        ]

        backend = PGVectorBackend()
        ids = backend.add_documents(fake_vs, docs)

        self.assertEqual(ids, ["t1", "t2"])
        self.assertEqual(fake_vs._texts, ["a", "b"])
        self.assertEqual(fake_vs._metadatas, [{"k": 1}, {"k": 2}])

    def test_raises_when_neither_method_supported(self):
        """vector_store 既不支持 add_documents 也不支持 add_texts 时抛 ValueError。"""

        class BareStore:
            pass

        backend = PGVectorBackend()
        with self.assertRaises(ValueError):
            backend.add_documents(BareStore(), [Document(page_content="a")])


class DeleteTest(unittest.TestCase):
    """测试 ``delete()`` 删除集合操作。"""

    @patch(_CONNECTIONS_PATH)
    def test_returns_false_when_collection_table_missing(self, mock_connections):
        """collection 表不存在时返回 False。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),  # _get_collection_table_name
            ("langchain_pg_embedding",),  # _get_embedding_table_name
            (False,),  # _check_table_exists(collection) -> False
        ]

        backend = PGVectorBackend()
        result = backend.delete("my_collection")

        self.assertFalse(result)

    @patch(_CONNECTIONS_PATH)
    def test_delete_removes_embeddings_and_collection(self, mock_connections):
        """先删除 embedding 数据，再删除 collection 记录，返回 True。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),  # _get_collection_table_name
            ("langchain_pg_embedding",),  # _get_embedding_table_name
            (True,),  # _check_table_exists(collection)
        ]
        mock_cursor.rowcount = 1  # collection 删除 1 条 -> 返回 True

        backend = PGVectorBackend()
        result = backend.delete("my_collection")

        self.assertTrue(result)
        # 验证执行了两条 DELETE（embedding + collection）
        delete_sqls = [c.args[0] for c in mock_cursor.execute.call_args_list if c.args and "DELETE FROM" in c.args[0]]
        self.assertEqual(len(delete_sqls), 2)
        # 两条 DELETE 均使用包装后的表名
        self.assertTrue(all('"langchain_pg_' in sql for sql in delete_sqls))

    @patch(_CONNECTIONS_PATH)
    def test_returns_false_when_collection_not_found(self, mock_connections):
        """collection 记录不存在时返回 False。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),
            ("langchain_pg_embedding",),
            (True,),
        ]
        mock_cursor.rowcount = 0  # collection 删除 0 条

        backend = PGVectorBackend()
        result = backend.delete("nonexistent")

        self.assertFalse(result)


class RemoveDocumentsTest(unittest.TestCase):
    """测试 ``remove_documents()`` 按 ID 删除文档。"""

    def test_returns_true_for_empty_document_ids(self):
        """空 ID 列表直接返回 True，不查询数据库。"""
        backend = PGVectorBackend()
        result = backend.remove_documents("my_collection", [])
        self.assertTrue(result)

    @patch(_CONNECTIONS_PATH)
    def test_returns_false_when_embedding_table_missing(self, mock_connections):
        """embedding 表不存在时返回 False。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_embedding",),  # _get_embedding_table_name
            (False,),  # _check_table_exists(embedding) -> False
        ]

        backend = PGVectorBackend()
        result = backend.remove_documents("my_collection", ["doc1"])

        self.assertFalse(result)

    @patch(_CONNECTIONS_PATH)
    def test_returns_false_when_collection_not_found(self, mock_connections):
        """集合不存在（uuid 查询为空）时返回 False。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_embedding",),  # _get_embedding_table_name
            (True,),  # _check_table_exists(embedding)
            ("langchain_pg_collection",),  # _get_collection_table_name
            None,  # SELECT uuid ... fetchone -> 集合不存在
        ]

        backend = PGVectorBackend()
        result = backend.remove_documents("my_collection", ["doc1"])

        self.assertFalse(result)

    @patch(_CONNECTIONS_PATH)
    def test_deletes_by_document_ids(self, mock_connections):
        """按 ID 删除文档成功，返回 True。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_embedding",),
            (True,),
            ("langchain_pg_collection",),
            ("collection-uuid-123",),  # SELECT uuid
        ]
        mock_cursor.rowcount = 2

        backend = PGVectorBackend()
        result = backend.remove_documents("my_collection", ["doc1", "doc2"])

        self.assertTrue(result)
        # 验证 DELETE 使用了占位符传参（而非字符串拼接），避免注入
        delete_calls = [c for c in mock_cursor.execute.call_args_list if c.args and "DELETE FROM" in c.args[0]]
        self.assertEqual(len(delete_calls), 1)
        sql, params = delete_calls[0].args
        self.assertIn("id::text IN", sql)
        self.assertEqual(params, ["collection-uuid-123", "doc1", "doc2"])


class RemoveDocumentsByMetadataTest(unittest.TestCase):
    """测试 ``remove_documents_by_metadata()`` 按元数据精确匹配删除。"""

    @patch(_CONNECTIONS_PATH)
    def test_returns_zero_when_tables_missing(self, mock_connections):
        """表不存在时返回 0。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_embedding",),
            ("langchain_pg_collection",),
            (False,),  # _check_table_exists(embedding) -> False
        ]

        backend = PGVectorBackend()
        result = backend.remove_documents_by_metadata("c", "source", "f.txt")

        self.assertEqual(result, 0)

    @patch(_CONNECTIONS_PATH)
    def test_returns_zero_when_no_matching_docs(self, mock_connections):
        """无匹配文档时返回 0，不执行 DELETE。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = []

        backend = PGVectorBackend()
        result = backend.remove_documents_by_metadata("c", "source", "none")

        self.assertEqual(result, 0)
        delete_calls = [c for c in mock_cursor.execute.call_args_list if c.args and "DELETE FROM" in c.args[0]]
        self.assertEqual(len(delete_calls), 0)

    @patch(_CONNECTIONS_PATH)
    def test_deletes_matching_docs_and_returns_count(self, mock_connections):
        """删除匹配文档并返回删除数量。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = [("uuid-1",), ("uuid-2",)]
        mock_cursor.rowcount = 2

        backend = PGVectorBackend()
        result = backend.remove_documents_by_metadata("c", "source", "f.txt")

        self.assertEqual(result, 2)
        # 验证 SELECT 使用了参数化查询
        select_calls = [c for c in mock_cursor.execute.call_args_list if c.args and "SELECT id FROM" in c.args[0]]
        self.assertEqual(len(select_calls), 1)
        _, select_params = select_calls[0].args
        self.assertEqual(select_params, ["c", "source", "f.txt"])


class RemoveDocumentsByMetadataLikeTest(unittest.TestCase):
    """测试 ``remove_documents_by_metadata_like()`` 按元数据 LIKE 模式删除。"""

    @patch(_CONNECTIONS_PATH)
    def test_returns_zero_when_no_matching_docs(self, mock_connections):
        """无匹配文档时返回 0。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = []

        backend = PGVectorBackend()
        result = backend.remove_documents_by_metadata_like("c", "source", "tmp_%")

        self.assertEqual(result, 0)

    @patch(_CONNECTIONS_PATH)
    def test_uses_like_pattern_in_query(self, mock_connections):
        """SELECT 使用 LIKE 参数化查询。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = _fetchone_tables_exist()
        mock_cursor.fetchall.return_value = [("uuid-1",)]
        mock_cursor.rowcount = 1

        backend = PGVectorBackend()
        result = backend.remove_documents_by_metadata_like("c", "source", "tmp_%")

        self.assertEqual(result, 1)
        select_calls = [c for c in mock_cursor.execute.call_args_list if c.args and "SELECT id FROM" in c.args[0]]
        self.assertEqual(len(select_calls), 1)
        sql, params = select_calls[0].args
        self.assertIn("LIKE %s", sql)
        self.assertEqual(params, ["c", "source", "tmp_%"])


class SearchTest(unittest.TestCase):
    """测试 ``search()`` 委托到底层 vector_store。"""

    def test_delegates_to_similarity_search_with_score(self):
        """调用 ``similarity_search_with_score`` 并返回结果。"""
        mock_vs = MagicMock()
        mock_doc = MagicMock()
        mock_vs.similarity_search_with_score.return_value = [(mock_doc, 0.5)]

        backend = PGVectorBackend()
        result = backend.search(mock_vs, "query", k=4)

        self.assertEqual(result, [(mock_doc, 0.5)])
        mock_vs.similarity_search_with_score.assert_called_once_with(query="query", k=4)

    def test_passes_filter_when_provided(self):
        """提供 filter 时传入 ``filter`` 关键字。"""
        mock_vs = MagicMock()
        backend = PGVectorBackend()
        backend.search(mock_vs, "query", k=2, filter={"source": "a"})

        mock_vs.similarity_search_with_score.assert_called_once_with(query="query", k=2, filter={"source": "a"})

    def test_omits_filter_when_none(self):
        """filter 为 None 时不传入 ``filter`` 关键字。"""
        mock_vs = MagicMock()
        backend = PGVectorBackend()
        backend.search(mock_vs, "query", k=2, filter=None)

        _, kwargs = mock_vs.similarity_search_with_score.call_args
        self.assertNotIn("filter", kwargs)

    def test_default_k_is_four(self):
        """默认 k=4。"""
        mock_vs = MagicMock()
        backend = PGVectorBackend()
        backend.search(mock_vs, "query")

        _, kwargs = mock_vs.similarity_search_with_score.call_args
        self.assertEqual(kwargs["k"], 4)


class ListCollectionsTest(unittest.TestCase):
    """测试 ``list_collections()``。"""

    @patch(_CONNECTIONS_PATH)
    def test_returns_empty_when_table_missing(self, mock_connections):
        """collection 表不存在时返回空列表。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),  # _get_collection_table_name
            (False,),  # _check_table_exists -> False
        ]

        backend = PGVectorBackend()
        result = backend.list_collections()

        self.assertEqual(result, [])

    @patch(_CONNECTIONS_PATH)
    def test_lists_all_without_prefix(self, mock_connections):
        """无前缀时列出所有集合名。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),
            (True,),
        ]
        mock_cursor.fetchall.return_value = [("coll1",), ("coll2",)]

        backend = PGVectorBackend()
        result = backend.list_collections()

        self.assertEqual(result, ["coll1", "coll2"])

    @patch(_CONNECTIONS_PATH)
    def test_filters_by_prefix(self, mock_connections):
        """有前缀时使用 LIKE 参数化查询。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),
            (True,),
        ]
        mock_cursor.fetchall.return_value = [("coll1",)]

        backend = PGVectorBackend()
        result = backend.list_collections(prefix="coll")

        self.assertEqual(result, ["coll1"])
        select_calls = [c for c in mock_cursor.execute.call_args_list if c.args and "SELECT name FROM" in c.args[0]]
        self.assertEqual(len(select_calls), 1)
        sql, params = select_calls[0].args
        self.assertIn("LIKE %s", sql)
        self.assertEqual(params, ["coll%"])


class ExistsTest(unittest.TestCase):
    """测试 ``exists()``。"""

    @patch(_CONNECTIONS_PATH)
    def test_returns_false_when_table_missing(self, mock_connections):
        """collection 表不存在时返回 False。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),
            (False,),
        ]

        backend = PGVectorBackend()
        self.assertFalse(backend.exists("c"))

    @patch(_CONNECTIONS_PATH)
    def test_returns_true_when_collection_exists(self, mock_connections):
        """集合存在时返回 True。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),
            (True,),  # _check_table_exists
            (True,),  # SELECT EXISTS ... fetchone[0]
        ]

        backend = PGVectorBackend()
        self.assertTrue(backend.exists("my_collection"))

    @patch(_CONNECTIONS_PATH)
    def test_returns_false_when_collection_not_exists(self, mock_connections):
        """集合不存在时返回 False。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),
            (True,),
            (False,),
        ]

        backend = PGVectorBackend()
        self.assertFalse(backend.exists("nonexistent"))


class GetStatsTest(unittest.TestCase):
    """测试 ``get_stats()``。"""

    @patch(_CONNECTIONS_PATH)
    def test_returns_not_exists_when_table_missing(self, mock_connections):
        """collection 表不存在时返回 exists=False。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),  # _get_collection_table_name
            ("langchain_pg_embedding",),  # _get_embedding_table_name
            (False,),  # _check_table_exists(collection) -> False
        ]

        backend = PGVectorBackend()
        stats = backend.get_stats("my_collection")

        self.assertEqual(stats["name"], "my_collection")
        self.assertFalse(stats["exists"])
        self.assertEqual(stats["store_type"], "pgvector")

    @patch(_CONNECTIONS_PATH)
    def test_returns_not_exists_when_collection_not_found(self, mock_connections):
        """集合不存在时返回 exists=False。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),
            ("langchain_pg_embedding",),
            (True,),  # _check_table_exists(collection)
            None,  # SELECT uuid ... fetchone -> 集合不存在
        ]

        backend = PGVectorBackend()
        stats = backend.get_stats("my_collection")

        self.assertFalse(stats["exists"])
        self.assertEqual(stats["store_type"], "pgvector")

    @patch(_CONNECTIONS_PATH)
    def test_returns_stats_with_document_count(self, mock_connections):
        """集合存在时返回 exists=True 与文档数量。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),  # _get_collection_table_name
            ("langchain_pg_embedding",),  # _get_embedding_table_name
            (True,),  # _check_table_exists(collection)
            ("collection-uuid",),  # SELECT uuid
            (True,),  # _check_table_exists(embedding)
            (42,),  # SELECT COUNT(*) -> 42 个文档
        ]

        backend = PGVectorBackend()
        stats = backend.get_stats("my_collection")

        self.assertTrue(stats["exists"])
        self.assertEqual(stats["store_type"], "pgvector")
        self.assertEqual(stats["num_documents"], 42)
        self.assertEqual(stats["name"], "my_collection")

    @patch(_CONNECTIONS_PATH)
    def test_returns_zero_count_when_embedding_table_missing(self, mock_connections):
        """embedding 表不存在时文档数量为 0。"""
        mock_cursor = MagicMock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
        mock_connections.__getitem__.return_value.ops.quote_name.side_effect = _pg_quote_name
        mock_cursor.fetchone.side_effect = [
            ("langchain_pg_collection",),
            ("langchain_pg_embedding",),
            (True,),  # _check_table_exists(collection)
            ("collection-uuid",),
            (False,),  # _check_table_exists(embedding) -> False
        ]

        backend = PGVectorBackend()
        stats = backend.get_stats("my_collection")

        self.assertTrue(stats["exists"])
        self.assertEqual(stats["num_documents"], 0)


class StoreTypeTest(unittest.TestCase):
    """测试 ``store_type`` 属性。"""

    def test_store_type_is_pgvector(self):
        backend = PGVectorBackend()
        self.assertEqual(backend.store_type, "pgvector")


if __name__ == "__main__":
    unittest.main()
