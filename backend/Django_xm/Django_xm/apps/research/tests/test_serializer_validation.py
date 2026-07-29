"""深度研究 Serializer 校验完整性测试（Task 13.3）。

覆盖 spec `fix-backend-audit-findings` SubTask 13.3：
- ResearchStartSerializer.query max_length=10000
- ResearchStartSerializer.research_depth 限定 ['basic', 'standard', 'comprehensive']

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe -m pytest \
        Django_xm/apps/research/tests/test_serializer_validation.py -v --tb=short
"""

from __future__ import annotations

import os
import unittest

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.research.serializers import (
    RESEARCH_DEPTH_CHOICES,
    ResearchStartSerializer,
)


class ResearchStartSerializerQueryValidationTest(unittest.TestCase):
    """ResearchStartSerializer.query max_length=10000 测试。"""

    def test_valid_short_query_passes(self) -> None:
        """合法短查询通过校验。"""
        s = ResearchStartSerializer(data={"query": "量子计算最新进展"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_query_at_max_length_passes(self) -> None:
        """正好 10000 字符的 query 通过校验。"""
        query = "a" * 10000
        s = ResearchStartSerializer(data={"query": query})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_query_above_max_length_rejected(self) -> None:
        """超过 10000 字符的 query 被拒绝。"""
        query = "a" * 10001
        s = ResearchStartSerializer(data={"query": query})
        self.assertFalse(s.is_valid())
        self.assertIn("query", s.errors)

    def test_empty_query_rejected(self) -> None:
        """空 query 被拒绝（min_length=1）。"""
        s = ResearchStartSerializer(data={"query": ""})
        self.assertFalse(s.is_valid())
        self.assertIn("query", s.errors)

    def test_missing_query_rejected(self) -> None:
        """缺少 query 字段被拒绝。"""
        s = ResearchStartSerializer(data={})
        self.assertFalse(s.is_valid())
        self.assertIn("query", s.errors)


class ResearchStartSerializerDepthValidationTest(unittest.TestCase):
    """ResearchStartSerializer.research_depth 枚举校验测试。"""

    def test_default_depth_is_standard(self) -> None:
        """缺省 research_depth 为 'standard'。"""
        s = ResearchStartSerializer(data={"query": "q"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))
        self.assertEqual(s.validated_data["research_depth"], "standard")

    def test_basic_depth_passes(self) -> None:
        """research_depth='basic' 通过校验。"""
        s = ResearchStartSerializer(data={"query": "q", "research_depth": "basic"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))
        self.assertEqual(s.validated_data["research_depth"], "basic")

    def test_standard_depth_passes(self) -> None:
        """research_depth='standard' 通过校验。"""
        s = ResearchStartSerializer(data={"query": "q", "research_depth": "standard"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_comprehensive_depth_passes(self) -> None:
        """research_depth='comprehensive' 通过校验。"""
        s = ResearchStartSerializer(data={"query": "q", "research_depth": "comprehensive"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_invalid_depth_rejected(self) -> None:
        """非法 research_depth 被拒绝。"""
        s = ResearchStartSerializer(data={"query": "q", "research_depth": "invalid"})
        self.assertFalse(s.is_valid())
        self.assertIn("research_depth", s.errors)

    def test_empty_depth_rejected(self) -> None:
        """空字符串 research_depth 被拒绝。"""
        s = ResearchStartSerializer(data={"query": "q", "research_depth": ""})
        self.assertFalse(s.is_valid())
        self.assertIn("research_depth", s.errors)

    def test_choices_constant_matches(self) -> None:
        """RESEARCH_DEPTH_CHOICES 常量与允许值匹配。"""
        self.assertEqual(
            set(RESEARCH_DEPTH_CHOICES),
            {"basic", "standard", "comprehensive"},
        )


class ResearchStartSerializerCrossFieldValidationTest(unittest.TestCase):
    """跨字段校验测试：enable_doc_analysis 与 knowledge_base_ids 联动。"""

    def test_doc_analysis_without_kb_rejected(self) -> None:
        """启用文档分析但未提供 knowledge_base_ids 被拒绝。"""
        s = ResearchStartSerializer(
            data={
                "query": "q",
                "enable_doc_analysis": True,
                "knowledge_base_ids": [],
            }
        )
        self.assertFalse(s.is_valid())
        self.assertTrue(s.errors)  # 至少有一个错误

    def test_doc_analysis_with_kb_passes(self) -> None:
        """启用文档分析且提供 knowledge_base_ids 通过校验。"""
        s = ResearchStartSerializer(
            data={
                "query": "q",
                "enable_doc_analysis": True,
                "knowledge_base_ids": ["kb-001"],
            }
        )
        self.assertTrue(s.is_valid(), msg=str(s.errors))


if __name__ == "__main__":
    unittest.main()
