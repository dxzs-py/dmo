"""B-2 RRF 融合纯函数单源测试（rrf_fuse）。

此前同一 RRF 算法两处内联且能力不同步（views_rag.SearchAllView 等权保留
score / UnifiedRagPipeline.search_multi_kb 带权重无 score）；收敛到
rag_retrieval.rrf_fuse 后取能力并集：权重 + first_score 透出 + 首现库索引，
两调用方（SearchAllView / search_multi_kb）共用本实现。

数学语义锁定：fused_score = Σ weight / (rrf_k + rank)，rank 从 1 起；
同分保持首现序（稳定排序）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rrf_fuse --noinput
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.rag_retrieval import rrf_fuse


def _doc(content: str) -> Document:
    return Document(page_content=content, metadata={})


class RrfFuseTests(unittest.TestCase):
    def test_equal_weight_fusion_math(self):
        """等权融合数学值：两个库各自 rank1 的同文 doc，fused = 1/(k+1) + 1/(k+1)。"""
        d1 = _doc("跨库同文")
        d2 = _doc("仅库二")
        ranked = [
            [(d1, 0.9), (d2, 0.8)],
            [(d1, 0.95)],
        ]
        fused = rrf_fuse(ranked, weights=[1.0, 1.0], rrf_k=60)

        self.assertEqual(fused[0][0], d1)
        self.assertAlmostEqual(fused[0][1], 1.0 / 61 + 1.0 / 61)
        self.assertEqual(fused[1][0], d2)
        # d2 仅库 0 rank2：1/(60+2)
        self.assertAlmostEqual(fused[1][1], 1.0 / 62)

    def test_weighted_fusion_changes_order(self):
        """权重加权：高权重库的 rank1 文档可超过两库累加的低权重库文档。"""
        a = _doc("A")
        b = _doc("B")
        ranked = [
            [(a, 0.9)],
            [(b, 0.5), (a, 0.4)],
        ]
        # 库二权重 100：b(rank1) = 100/61 ≈ 1.6393；a(rank1+rank2) = 1/61 + 100/62 ≈ 1.6293
        fused = rrf_fuse(ranked, weights=[1.0, 100.0], rrf_k=60)
        self.assertEqual(fused[0][0], b)

    def test_first_score_and_source_index_preserved(self):
        """能力并集：first_score 为首现库原生 score，first_list_idx 指向首现库。"""
        a = _doc("A")
        b = _doc("B")
        ranked = [
            [(a, 0.9), (b, None)],
            [(a, 0.95), (b, 0.7)],
        ]
        fused = rrf_fuse(ranked, weights=None, rrf_k=60)

        by_doc = {doc.page_content: (score, src_idx) for doc, _f, score, src_idx in fused}
        # a 两库都出现，first = 库 0 的 0.9
        self.assertEqual(by_doc["A"], (0.9, 0))
        # b 首现于库 0 且 score=None
        self.assertEqual(by_doc["B"], (None, 0))

    def test_first_source_index_after_failed_list(self):
        """首现库索引映射：idx 指向紧凑列表内位置（调用方以平行 source 列表映射）。"""
        a = _doc("A")
        c = _doc("C")
        ranked = [
            [(c, 0.5)],
            [(a, 0.9)],
        ]
        fused = rrf_fuse(ranked, rrf_k=60)
        # c 与 a 同 rank1 同分 → 稳定排序保持首现序：c(idx 0) 在前，a(idx 1) 在后
        self.assertEqual(fused[0][0], c)
        self.assertEqual(fused[0][3], 0)
        self.assertEqual(fused[1][0], a)
        self.assertEqual(fused[1][3], 1)

    def test_stable_order_on_tie(self):
        """同分保持首现序（稳定排序）：两库各自独有 doc 且同 rank 同权重。"""
        a = _doc("A")
        b = _doc("B")
        ranked = [
            [(a, None)],
            [(b, None)],
        ]
        fused = rrf_fuse(ranked, rrf_k=60)
        self.assertEqual([f[0].page_content for f in fused], ["A", "B"])

    def test_empty_lists(self):
        self.assertEqual(rrf_fuse([]), [])
        self.assertEqual(rrf_fuse([[]]), [])


if __name__ == "__main__":
    unittest.main()
