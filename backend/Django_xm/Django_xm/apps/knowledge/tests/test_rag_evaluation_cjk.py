"""RP-05：keyword_overlap 对 CJK 文本恒 0 → CJK 感知分词修复。

原实现恒 ``text.lower().split()``：中文无空格 → 整句单个 token，查询与文本
的词集交集恒空 → 重叠恒 0（评估重试判据恒真、重检后分数仍 0，无收敛）。

修复：``_tokenize_for_overlap`` —— 拉丁词保持整词匹配（与原 split 行为等价），
CJK 连续段按二元组（bigram）匹配（仅单字独立成段时保留单字），混合块拉丁
片段整词、CJK 段二元组。``_heuristic_evaluate_generation`` 的
faithfulness/completeness 内联 split 存在同型缺陷，一并切换到该分词器。

载体：management command evaluate_rag（RAGEvaluator 消费方）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_evaluation_cjk --noinput
（纯单元测试，无 DB / Redis / LLM 依赖）
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.rag_evaluation import (
    _tokenize_for_overlap,
    keyword_overlap,
)


class KeywordOverlapCjkTests(unittest.TestCase):
    """修复前 keyword_overlap("知识库检索", "...知识库系统") 恒 0.0（红基线）。"""

    def test_cjk_overlap_positive(self):
        """中文查询与包含同词的文本重叠 > 0（修复前恒 0）。"""
        self.assertGreater(keyword_overlap("知识库检索", "这是一个知识库系统，支持检索"), 0.0)

    def test_cjk_full_overlap_is_one(self):
        """查询词完全包含于文本 → 重叠 1.0。"""
        self.assertAlmostEqual(keyword_overlap("知识库", "知识库系统"), 1.0)

    def test_cjk_no_overlap_is_zero(self):
        """语义无关的中文文本 → 重叠 0（分词器不产生伪命中）。"""
        self.assertEqual(keyword_overlap("知识库检索", "天气预报说明天有雨"), 0.0)

    def test_empty_query_is_zero(self):
        self.assertEqual(keyword_overlap("", "任意文本"), 0.0)
        self.assertEqual(keyword_overlap("   ", "任意文本"), 0.0)

    def test_latin_behavior_unchanged(self):
        """拉丁词仍按整词匹配（原 split 行为保持，含大小写归一）。"""
        self.assertAlmostEqual(keyword_overlap("langchain framework", "langchain is a framework"), 1.0)
        self.assertAlmostEqual(keyword_overlap("langchain", "LangChain Rocks"), 1.0)
        self.assertEqual(keyword_overlap("langchain", "django"), 0.0)

    def test_mixed_script_chunk(self):
        """混合块："文本embedding模型" → CJK 段二元组 + 拉丁片段整词均可命中。"""
        self.assertGreater(keyword_overlap("文本embedding模型", "用文本embedding模型生成向量"), 0.0)

    def test_tokenizer_split_bigrams(self):
        """分词器契约：CJK 连续段产出二元组，拉丁段产出整词。"""
        self.assertEqual(_tokenize_for_overlap("知识库"), {"知识", "识库"})
        self.assertEqual(_tokenize_for_overlap("hello world"), {"hello", "world"})
        self.assertEqual(_tokenize_for_overlap("问"), {"问"})


class HeuristicGenerationCjkTests(unittest.TestCase):
    """_heuristic_evaluate_generation 内联 split 同型缺陷修复表征。"""

    def test_cjk_metrics_positive(self):
        """中文查询/回答/源文档 → relevance/faithfulness/completeness 均 > 0。"""
        from Django_xm.apps.knowledge.services.rag_evaluation import RAGEvaluator

        evaluator = RAGEvaluator(llm=None)
        metrics = evaluator._heuristic_evaluate_generation(
            "知识库检索",
            "本系统的知识库检索支持多路召回",
            [Document(page_content="知识库检索模块介绍", metadata={})],
        )
        self.assertGreater(metrics.relevance, 0.0)
        self.assertGreater(metrics.faithfulness, 0.0)
        self.assertGreater(metrics.completeness, 0.0)


if __name__ == "__main__":
    unittest.main()
