"""C1（KV-2）检索分数语义回归测试：pgvector cosine distance 被当相似度使用。

契约（remediate-rag-ai-correctness C1）：
1. `search_vector_store` 的 score_threshold 语义 = 相似度（越大越相关，与
   config retriever_score_threshold/rag_score_threshold 的 0~1 相似度口径一致）；
2. 后端返回 cosine **distance**（langchain_postgres 0.0.17 实测，越小越相关，
   列表按 distance 升序 = 最相关在前）时，必须经 score_semantics="distance"
   归一（similarity = 1 - distance）后再过滤/返回；
3. 默认 score_semantics="similarity"（InMemory 等相似度语义后端）行为不变。

损坏复现（修复前）：pgvector 路径 `score >= threshold` 是反向过滤——
最相关文档（distance 最小）最先被淘汰，阈值越高留下的越不相关；
views_rag SearchAllView 按 score 降序做库内 RRF 排名 → 最差文档 rank=1。

运行方式：
   python manage.py test Django_xm.apps.knowledge.tests.test_search_score_semantics --noinput
"""

from django.test import SimpleTestCase

from Django_xm.apps.knowledge.vector_store import search_vector_store


class _FakeDistanceStore:
    """模拟 langchain_postgres 0.0.17：返回 (doc, cosine distance)，按 distance 升序。"""

    def __init__(self, items=None):
        # (page_content, distance)：docA 最相关，docC 最不相关
        self.items = items or [
            ("docA 相关", 0.1),
            ("docB 中等", 0.4),
            ("docC 无关", 1.2),
        ]

    def similarity_search_with_score(self, query, k=4, **kwargs):
        return [(type("Doc", (), {"page_content": c})(), d) for c, d in self.items[:k]]


class _FakeSimilarityStore:
    """模拟 InMemoryVectorStore：返回 cosine similarity（越大越相关）。"""

    def similarity_search_with_score(self, query, k=4, **kwargs):
        items = [("docA 相关", 0.9), ("docB 中等", 0.6), ("docC 无关", -0.2)]
        return [(type("Doc", (), {"page_content": c})(), s) for c, s in items[:k]]


class DistanceSemanticsTests(SimpleTestCase):
    def test_distance_mode_filters_by_similarity(self):
        """修复后：distance 归一为相似度后再按阈值过滤。"""
        results = search_vector_store(
            _FakeDistanceStore(), "q", k=3, score_threshold=0.8, score_semantics="distance"
        )
        contents = [doc.page_content for doc, _score in results]
        self.assertEqual(contents, ["docA 相关"])
        self.assertAlmostEqual(results[0][1], 0.9)

    def test_distance_mode_order_preserved(self):
        """归一后顺序仍是相关度降序（pgvector 原序 = distance 升序）。"""
        results = search_vector_store(
            _FakeDistanceStore(), "q", k=3, score_threshold=None, score_semantics="distance"
        )
        contents = [doc.page_content for doc, _score in results]
        self.assertEqual(contents, ["docA 相关", "docB 中等", "docC 无关"])
        scores = [score for _doc, score in results]
        for actual, expected in zip(scores, [0.9, 0.6, -0.2], strict=True):
            self.assertAlmostEqual(actual, expected)

    def test_distance_mode_none_score_passthrough(self):
        """score 为 None 的条目（如后端异常路径）归一时保留 None。"""
        store = _FakeDistanceStore(items=[("docX", 0.1), ("docY", None)])
        results = search_vector_store(
            store, "q", k=2, score_threshold=None, score_semantics="distance"
        )
        self.assertAlmostEqual(results[0][1], 0.9)
        self.assertIsNone(results[1][1])


class SimilarityDefaultRegressionTests(SimpleTestCase):
    """默认 similarity 模式行为不变（InMemory 等后端回归保护）。"""

    def test_similarity_mode_threshold(self):
        results = search_vector_store(
            _FakeSimilarityStore(), "q", k=3, score_threshold=0.8
        )
        contents = [doc.page_content for doc, _score in results]
        self.assertEqual(contents, ["docA 相关"])

    def test_similarity_mode_no_threshold(self):
        results = search_vector_store(_FakeSimilarityStore(), "q", k=3)
        self.assertEqual(len(results), 3)
