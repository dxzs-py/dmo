"""应用层 BM25 回归（CJK 2-gram tokenizer + Okapi 打分）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_bm25_index --noinput
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.bm25_index import BM25Index, tokenize


class TokenizeTests(unittest.TestCase):
    def test_cjk_bigram_and_ascii_words(self):
        tokens = tokenize("Redis 持久化机制")

        self.assertIn("redis", tokens)
        self.assertIn("持久", tokens)
        self.assertIn("久化", tokens)
        self.assertIn("化机", tokens)
        self.assertIn("机制", tokens)

    def test_repeats_preserved_for_tf(self):
        tokens = tokenize("缓存缓存")
        self.assertEqual(tokens.count("缓存"), 2)

    def test_single_cjk_char_kept(self):
        self.assertEqual(tokenize("锁"), ["锁"])


class BM25IndexTests(unittest.TestCase):
    def _docs(self):
        return [
            Document(page_content="Redis 持久化使用 RDB 与 AOF 两种机制", metadata={"source": "redis.md"}),
            Document(page_content="Python 的列表推导式可以简化循环写法", metadata={"source": "py.md"}),
            Document(page_content="Redis 集群通过分片实现水平扩展", metadata={"source": "cluster.md"}),
        ]

    def test_most_relevant_doc_first(self):
        index = BM25Index(self._docs())

        result = index.search("Redis 持久化 RDB", k=2)

        self.assertEqual(result[0].metadata["source"], "redis.md")

    def test_rare_term_has_higher_idf(self):
        index = BM25Index(self._docs())

        result = index.search("AOF", k=3)

        self.assertEqual([doc.metadata["source"] for doc in result], ["redis.md"])

    def test_unmatched_query_returns_empty(self):
        index = BM25Index(self._docs())
        self.assertEqual(index.search("Kubernetes", k=3), [])

    def test_empty_query_returns_empty(self):
        index = BM25Index(self._docs())
        self.assertEqual(index.search("", k=3), [])

    def test_k_limits_results(self):
        index = BM25Index(self._docs())
        self.assertLessEqual(len(index.search("Redis", k=1)), 1)


if __name__ == "__main__":
    unittest.main()
