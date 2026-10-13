"""知识库检索降级单元测试（向量检索失败 → PG 全文关键词检索）。

覆盖 Django_xm.apps.knowledge.services.keyword_fallback（B-3/A-4 拆分转正）：
- is_embedding_error 表驱动（B14 收窄契约）：类型化判定（内置连接类异常）+
  字符串兜底仅匹配 embedding 明确特征词（embedding / ollama /
  insufficient_balance 等）；过宽子串（connect / timeout / refused / 403 /
  503）不再命中——PG 连接错误误判会转入关键词降级再打同一 PG；
  PG/Django.db/SQLAlchemy 数据库层异常显式排除（详见
  test_rag_details_b9_b14.IsEmbeddingErrorNarrowedTests）
- keyword_search_fallback：
  * PGVector 表不存在 → 返回 []
  * 表存在 + mock DB cursor → 返回 Document 列表，metadata 含 degraded=True
    与 degraded_score（float(rank)），cmetadata 为 None 时落 {}
  * k 参数透传到 SQL LIMIT %s（捕获 cursor.execute 参数断言）
  * CJK 2-gram / ASCII 整词切分（build_retrieval_grams），gram 数组参数化传入
  * DB 异常 → 返回 []（降级链最终兜底）

mock 点说明：keyword_search_fallback 函数体内延迟导入
``from Django_xm.apps.knowledge.vector_store.pgvector_backend import ...`` 与
``from django.db import connections``，均为调用时解析，故：
- pgvector_backend 的 check_table_exists / get_collection_table_name /
  get_embedding_table_name / quote_identifier patch 其源模块属性
- django.db.connections 整体替换为 MagicMock，其
  ``connections["default"].cursor()`` 上下文管理器返回受控 cursor
（keyword_search_fallback 使用 PG 专属 unnest + ILIKE 原生 SQL，必须 mock DB 层）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.knowledge.tests.test_retrieval_fallback
（纯单元测试，无 DB / Redis / LLM 依赖）
"""

import os
import unittest
from typing import Any, ClassVar
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.keyword_fallback import (
    build_retrieval_grams,
    is_embedding_error,
    keyword_search_fallback,
)

_PGV_BACKEND = "Django_xm.apps.knowledge.vector_store.pgvector_backend"


class IsEmbeddingErrorTests(unittest.TestCase):
    """is_embedding_error：B14 收窄契约（类型化优先 + 字符串仅特征词）。"""

    _CASES: ClassVar[list[tuple[Exception, bool]]] = [
        # 类型化命中：内置连接类异常（类型判定优先，不依赖消息内容）
        (ConnectionError("dial tcp failed"), True),
        (ConnectionRefusedError("errno 111"), True),
        (TimeoutError("request timed out"), True),
        # 字符串兜底（B14 收窄）：仅 embedding 明确特征词 / provider 名
        (RuntimeError("Ollama Server Error"), True),
        (RuntimeError("embedding service unavailable"), True),
        (Exception("insufficient_balance"), True),
        # B14 收窄反例：过宽子串不再命中（旧行为会把 PG/网络错误误判为降级）
        (OSError("[Errno 111] Connection refused"), False),
        (RuntimeError("cannot connect to host"), False),
        (RuntimeError("timeout while parsing payload"), False),
        (Exception("HTTP 403 Forbidden"), False),
        (Exception("service unavailable: 503"), False),
        (Exception("MaxRetriesError: too many retries"), False),
        # 普通业务异常：不触发降级
        (ValueError("invalid arg"), False),
        (KeyError("missing key"), False),
        (TypeError("unsupported operand type"), False),
        (AssertionError("assertion failed"), False),
    ]

    def test_embedding_error_pattern_table(self) -> None:
        """表驱动验证：类名或消息（均小写化）命中任一模式 → True。"""
        for exc, expected in self._CASES:
            with self.subTest(exc=repr(exc)):
                self.assertIs(is_embedding_error(exc), expected)

    def test_matching_is_case_insensitive(self) -> None:
        """大小写不敏感：消息含大写 Ollama 也命中。"""
        self.assertTrue(is_embedding_error(RuntimeError("OLLAMA backend down")))

    def test_business_exception_returns_false(self) -> None:
        """任务指定的反例：ValueError("invalid arg") 不属于 Embedding 服务故障。"""
        self.assertFalse(is_embedding_error(ValueError("invalid arg")))


class KeywordSearchFallbackTests(unittest.TestCase):
    """keyword_search_fallback：PG 全文检索降级路径。"""

    def _patch_backend_helpers(self, check_exists: bool) -> None:
        """打桩 pgvector_backend 辅助函数（函数内延迟导入，patch 源模块属性）。"""
        patchers = [
            mock.patch(f"{_PGV_BACKEND}.check_table_exists", return_value=check_exists),
            mock.patch(
                f"{_PGV_BACKEND}.get_collection_table_name",
                return_value="langchain_pg_collection",
            ),
            mock.patch(
                f"{_PGV_BACKEND}.get_embedding_table_name",
                return_value="langchain_pg_embedding",
            ),
            mock.patch(
                f"{_PGV_BACKEND}.quote_identifier",
                side_effect=lambda name: f'"{name}"',
            ),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _make_connections_mock(self, cursor: mock.MagicMock) -> mock.MagicMock:
        """构造 connections 替身：connections["default"].cursor() 返回受控 cursor。"""
        connections_mock = mock.MagicMock()
        connections_mock.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
        return connections_mock

    def test_table_missing_returns_empty_list(self) -> None:
        """PGVector 表不存在：直接返回空结果，不执行检索 SQL。"""
        self._patch_backend_helpers(check_exists=False)
        cursor = mock.MagicMock()
        connections_mock = self._make_connections_mock(cursor)

        with mock.patch("django.db.connections", connections_mock):
            result = keyword_search_fallback("hello world", "test_collection", k=4)

        self.assertEqual(result, [])
        cursor.execute.assert_not_called()

    def test_returns_degraded_documents_with_k_passthrough(self) -> None:
        """表存在：行数据转 Document，metadata 标记降级；k 透传到 LIMIT 参数。"""
        self._patch_backend_helpers(check_exists=True)
        cursor = mock.MagicMock()
        # 第三列为该文档命中的 gram 数（count(*) 结果，上限为 gram 总数）
        cursor.fetchall.return_value = [
            ("文档内容甲", {"source": "a.md"}, 2),
            ("文档内容乙", None, 1),
        ]
        connections_mock = self._make_connections_mock(cursor)

        with mock.patch("django.db.connections", connections_mock):
            docs = keyword_search_fallback("hello world", "test_collection", k=7)

        # 返回 Document 列表，顺序与行数据一致
        self.assertEqual(len(docs), 2)
        for doc in docs:
            self.assertIsInstance(doc, Document)
        self.assertEqual(docs[0].page_content, "文档内容甲")
        self.assertEqual(docs[1].page_content, "文档内容乙")

        # metadata：degraded=True + degraded_hits + degraded_score(命中率)；
        # "hello world" 切出 ["hello","world"] 共 2 个 gram，故 score = hits / 2
        self.assertEqual(
            docs[0].metadata,
            {"source": "a.md", "degraded": True, "degraded_hits": 2, "degraded_score": 1.0},
        )
        self.assertEqual(
            docs[1].metadata,
            {"degraded": True, "degraded_hits": 1, "degraded_score": 0.5},
        )

        # SQL 断言：unnest + ILIKE 子串匹配（不再使用中文失效的 to_tsquery）+ k 透传到 LIMIT
        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args.args
        self.assertIn("unnest", sql)
        self.assertIn("ILIKE", sql)
        self.assertNotIn("to_tsquery", sql)
        self.assertIn("LIMIT %s", sql)
        self.assertIn('"langchain_pg_embedding"', sql)
        self.assertEqual(params, [["hello", "world"], "test_collection", 7])

    def test_gram_passthrough_and_default_k(self) -> None:
        """gram 数组参数化传入；缺省 k=4 透传到 LIMIT。"""
        self._patch_backend_helpers(check_exists=True)
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = []
        connections_mock = self._make_connections_mock(cursor)

        with mock.patch("django.db.connections", connections_mock):
            keyword_search_fallback("机器   学习 入门", "test_collection")

        params: list[Any] = cursor.execute.call_args.args[1]
        self.assertEqual(params[0], ["机器", "学习", "入门"])
        self.assertEqual(params[1], "test_collection")
        self.assertEqual(params[2], 4)

        # 单 gram 查询
        with mock.patch("django.db.connections", connections_mock):
            keyword_search_fallback("hello", "test_collection")
        params_single: list[Any] = cursor.execute.call_args.args[1]
        self.assertEqual(params_single[0], ["hello"])

    def test_query_without_retrievable_gram_skips_sql(self) -> None:
        """查询不含有效 gram（纯符号/空白/单 ASCII 字符）：不执行 SQL，直接返回 []。"""
        self._patch_backend_helpers(check_exists=True)
        cursor = mock.MagicMock()
        connections_mock = self._make_connections_mock(cursor)

        with mock.patch("django.db.connections", connections_mock):
            result = keyword_search_fallback("   !!! a ", "test_collection")

        self.assertEqual(result, [])
        cursor.execute.assert_not_called()

    def test_db_exception_returns_empty_list(self) -> None:
        """检索 SQL 抛异常：降级链最终兜底返回空结果。"""
        self._patch_backend_helpers(check_exists=True)
        cursor = mock.MagicMock()
        cursor.execute.side_effect = Exception("db down")
        connections_mock = self._make_connections_mock(cursor)

        with mock.patch("django.db.connections", connections_mock):
            result = keyword_search_fallback("hello", "test_collection", k=4)

        self.assertEqual(result, [])


class BuildRetrievalGramsTests(unittest.TestCase):
    """build_retrieval_grams：CJK 2-gram / ASCII 整词 / 去重 / 截断。"""

    def test_cjk_bigram_and_ascii_word(self) -> None:
        """中文切 2-gram；ASCII 词整词保留（长 gram 排在前面）。"""
        self.assertEqual(
            build_retrieval_grams("机器学习 hello"),
            ["hello", "机器", "器学", "学习"],
        )

    def test_single_ascii_char_dropped(self) -> None:
        """单个 ASCII 字符不构成检索单元（召回噪声过大）。"""
        self.assertEqual(build_retrieval_grams("a b 缓存"), ["缓存"])

    def test_duplicate_grams_deduped(self) -> None:
        """重复 gram 去重，避免同一子串被重复计数、抬高 degraded_score。"""
        self.assertEqual(build_retrieval_grams("缓存缓存"), ["缓存", "存缓"])

    def test_blank_query_returns_empty(self) -> None:
        self.assertEqual(build_retrieval_grams("   "), [])
        self.assertEqual(build_retrieval_grams(""), [])

    def test_max_grams_truncation(self) -> None:
        """长查询按 max_grams 截断，防止 SQL 开销失控。"""
        long_query = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸"
        self.assertEqual(len(build_retrieval_grams(long_query, max_grams=5)), 5)
        self.assertLess(len(build_retrieval_grams(long_query)), len(long_query))


if __name__ == "__main__":
    unittest.main()
