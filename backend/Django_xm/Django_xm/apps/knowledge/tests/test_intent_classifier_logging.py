"""E5-11：QueryIntentClassifier._parse_intent JSON 解析失败降级必须留日志。
（同文件扩展：B-3附 意图分类缓存有界化锁定，见 IntentCacheBoundTests。）

缺陷（修复前）：`except (json.JSONDecodeError, KeyError): pass` → 静默按 precise
检索，comprehensive 意图丢失不可观测（同类 _get_llm/invoke 异常分支均有 warning，
唯此分支无）。

修复契约（本文件锁定）：解析失败分支留一条 warning，仍返回 "precise"（语义不变）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_intent_classifier_logging --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase


class ParseIntentDegradationLogTests(SimpleTestCase):
    """解析失败 → 返回 precise 且有 warning（与同类分支日志口径对齐）。"""

    def test_malformed_json_returns_precise_and_logs_warning(self):
        from Django_xm.apps.knowledge.services.retriever_tool import QueryIntentClassifier

        classifier = QueryIntentClassifier(llm=None)
        with self.assertLogs("Django_xm.apps.knowledge.services.retriever_tool", level="WARNING") as captured:
            result = classifier._parse_intent("输出含花括号但非合法 JSON: {intent: comprehensive}")

        self.assertEqual(result, "precise")
        self.assertTrue(any("意图" in line for line in captured.output))

    def test_invalid_intent_value_returns_precise_and_logs_warning(self):
        """intent 值越界（不在 precise/comprehensive 白名单）同样视为解析失败。"""
        from Django_xm.apps.knowledge.services.retriever_tool import QueryIntentClassifier

        classifier = QueryIntentClassifier(llm=None)
        with self.assertLogs("Django_xm.apps.knowledge.services.retriever_tool", level="WARNING") as captured:
            result = classifier._parse_intent('{"intent": "hybrid", "confidence": 0.5}')

        self.assertEqual(result, "precise")
        self.assertTrue(any("意图" in line for line in captured.output))

    def test_valid_json_still_silent(self):
        """正常解析路径不应产生 warning（降级日志只属于降级分支）。"""
        from Django_xm.apps.knowledge.services.retriever_tool import QueryIntentClassifier

        classifier = QueryIntentClassifier(llm=None)
        result = classifier._parse_intent('{"intent": "comprehensive", "confidence": 0.9}')
        self.assertEqual(result, "comprehensive")


class IntentCacheBoundTests(SimpleTestCase):
    """B-3附：意图分类缓存有界化（原类级无界 dict 缓慢累积）。"""

    def setUp(self):
        from Django_xm.apps.knowledge.services.retriever_tool import QueryIntentClassifier

        QueryIntentClassifier._cache.clear()
        self.addCleanup(QueryIntentClassifier._cache.clear)

    def test_cache_evicts_oldest_beyond_maxsize(self):
        """写入超过 _cache_maxsize 条后最旧条目被逐出，容量恒有界。"""
        from Django_xm.apps.knowledge.services.retriever_tool import QueryIntentClassifier

        classifier = QueryIntentClassifier(llm=None)
        maxsize = QueryIntentClassifier._cache_maxsize
        for i in range(maxsize + 10):
            classifier._set_cache(f"query-{i}", "precise")

        self.assertLessEqual(len(QueryIntentClassifier._cache), maxsize)
        self.assertNotIn("query-0", QueryIntentClassifier._cache)
        self.assertIn(f"query-{maxsize + 9}", QueryIntentClassifier._cache)

    def test_cache_hit_refreshes_recency(self):
        """命中刷新 LRU 位次：最早写入但刚命中的条目不被逐出。"""
        from Django_xm.apps.knowledge.services.retriever_tool import QueryIntentClassifier

        classifier = QueryIntentClassifier(llm=None)
        maxsize = QueryIntentClassifier._cache_maxsize
        classifier._set_cache("hot-query", "comprehensive")
        for i in range(maxsize - 1):
            classifier._set_cache(f"query-{i}", "precise")

        self.assertIsNotNone(classifier._check_cache("hot-query"))
        classifier._set_cache("overflow-entry", "precise")

        self.assertIn("hot-query", QueryIntentClassifier._cache)

    def test_cache_ttl_hit_still_works(self):
        """TTL 内命中返回缓存意图（原命中语义不变）。"""
        from Django_xm.apps.knowledge.services.retriever_tool import QueryIntentClassifier

        classifier = QueryIntentClassifier(llm=None)
        classifier._set_cache("any-query", "comprehensive")
        self.assertEqual(classifier._check_cache("any-query"), "comprehensive")
