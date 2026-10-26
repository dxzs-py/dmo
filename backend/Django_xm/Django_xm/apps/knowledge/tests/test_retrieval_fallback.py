"""知识库检索降级单元测试（Phase 3：embedding 故障 → BM25 关键词检索）。

覆盖 Django_xm.apps.knowledge.services.keyword_fallback：
- is_embedding_error：B14 收窄契约（类型化优先 + 字符串仅 feature 词）
- keyword_search_fallback：BM25（bm25_index）降级路径
  * 索引不可用 / 构建异常 → 返回 []
  * 命中 → 返回 **Document 副本**（不污染 BM25 缓存对象），metadata 打 degraded 标记
  * mark_degraded=False → 仅标 sparse_mode（Hybrid/评测常开场景）
  * k 透传给 BM25 search

Phase 3 变更：pg_trgm 2-gram 子串匹配与 build_retrieval_grams 已删除，统一 BM25。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_retrieval_fallback --noinput
"""

import os
import unittest
from typing import ClassVar
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.keyword_fallback import (
    is_embedding_error,
    keyword_search_fallback,
)

_BM25 = "Django_xm.apps.knowledge.services.bm25_index"


class IsEmbeddingErrorTests(unittest.TestCase):
    """is_embedding_error：B14 收窄契约（类型化优先 + 字符串仅特征词）。"""

    _CASES: ClassVar[list[tuple[Exception, bool]]] = [
        (ConnectionError("dial tcp failed"), True),
        (ConnectionRefusedError("errno 111"), True),
        (TimeoutError("request timed out"), True),
        (RuntimeError("Ollama Server Error"), True),
        (RuntimeError("embedding service unavailable"), True),
        (Exception("insufficient_balance"), True),
        (OSError("[Errno 111] Connection refused"), False),
        (RuntimeError("cannot connect to host"), False),
        (RuntimeError("timeout while parsing payload"), False),
        (Exception("HTTP 403 Forbidden"), False),
        (Exception("service unavailable: 503"), False),
        (Exception("MaxRetriesError: too many retries"), False),
        (ValueError("invalid arg"), False),
        (KeyError("missing key"), False),
        (TypeError("unsupported operand type"), False),
        (AssertionError("assertion failed"), False),
    ]

    def test_embedding_error_pattern_table(self) -> None:
        for exc, expected in self._CASES:
            with self.subTest(exc=repr(exc)):
                self.assertIs(is_embedding_error(exc), expected)

    def test_matching_is_case_insensitive(self) -> None:
        self.assertTrue(is_embedding_error(RuntimeError("OLLAMA backend down")))

    def test_business_exception_returns_false(self) -> None:
        self.assertFalse(is_embedding_error(ValueError("invalid arg")))


class KeywordSearchFallbackTests(unittest.TestCase):
    """keyword_search_fallback：BM25 降级路径。"""

    def test_index_unavailable_returns_empty(self) -> None:
        with mock.patch(f"{_BM25}.get_bm25_index", return_value=None):
            self.assertEqual(keyword_search_fallback("hello", "kb", k=4), [])

    def test_returns_copied_docs_with_degraded_flag(self) -> None:
        doc = Document(page_content="文档内容", metadata={"source": "a.md"})
        index = mock.MagicMock(name="bm25_index")
        index.search.return_value = [doc]

        with mock.patch(f"{_BM25}.get_bm25_index", return_value=index):
            docs = keyword_search_fallback("查询", "kb", k=7)

        index.search.assert_called_once_with("查询", 7)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].page_content, "文档内容")
        self.assertTrue(docs[0].metadata["degraded"])
        self.assertEqual(docs[0].metadata["degraded_mode"], "bm25")
        # 缓存对象（入参 Document）不被污染
        self.assertNotIn("degraded", doc.metadata)

    def test_mark_degraded_false_marks_sparse_mode(self) -> None:
        doc = Document(page_content="内容", metadata={"source": "a.md"})
        index = mock.MagicMock()
        index.search.return_value = [doc]

        with mock.patch(f"{_BM25}.get_bm25_index", return_value=index):
            docs = keyword_search_fallback("查询", "kb", mark_degraded=False)

        self.assertNotIn("degraded", docs[0].metadata)
        self.assertEqual(docs[0].metadata["sparse_mode"], "bm25")

    def test_index_build_exception_returns_empty(self) -> None:
        with mock.patch(f"{_BM25}.get_bm25_index", side_effect=RuntimeError("pg down")):
            self.assertEqual(keyword_search_fallback("hello", "kb"), [])

    def test_empty_query_returns_empty(self) -> None:
        index = mock.MagicMock()
        index.search.return_value = []

        with mock.patch(f"{_BM25}.get_bm25_index", return_value=index):
            self.assertEqual(keyword_search_fallback("", "kb"), [])


if __name__ == "__main__":
    unittest.main()
