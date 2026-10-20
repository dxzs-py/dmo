"""C-4：RagResponseSerializer.sources 结构化子序列化器契约测试。

修复前缺口（rag_audit_chain C-4）：sources = ListField(child=CharField()) 对每条
结构化 dict 来源执行 str() → repr 字符串，与流式端点（_extract_sources 原样
json.dumps）形状漂移；前端 RagView "参考来源"面板渲染整段 repr 文本。

修复后契约：
- sources 元素为结构化对象 {content, source, metadata, [degraded]}，与流式链
  sources 事件 data 同构；
- 空 sources 列表 / 缺省 sources 不报错；
- 缓存命中路径（views_rag._prepare_request）与新鲜路径产出同一形状。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_response_serializer
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

import unittest
from typing import ClassVar

from rest_framework.exceptions import ValidationError

from Django_xm.apps.knowledge.serializers import RagResponseSerializer


class RagResponseSerializerSourcesTests(unittest.TestCase):
    """C-4：REST sources 结构化契约（与流式 sources 事件同构）。"""

    _STRUCTURED_SOURCE: ClassVar[dict] = {
        "content": "LangChain 是一个框架",
        "source": "intro.md",
        "metadata": {"file_name": "intro.md", "page": 3},
    }

    def test_structured_source_passes_through(self):
        """结构化 dict 来源逐字段透传，不再是 repr 字符串。"""
        serializer = RagResponseSerializer(
            {"answer": "答案", "sources": [self._STRUCTURED_SOURCE], "success": True}
        )
        data = serializer.data

        sources = data["sources"]
        self.assertEqual(len(sources), 1)
        item = sources[0]
        self.assertIsInstance(item, dict, "sources 元素必须是结构化对象（修复前是 dict repr 字符串）")
        self.assertEqual(item["content"], "LangChain 是一个框架")
        self.assertEqual(item["source"], "intro.md")
        self.assertEqual(item["metadata"], {"file_name": "intro.md", "page": 3})

    def test_degraded_flag_passes_through(self):
        """降级来源的 degraded 标记透传。"""
        source = {**self._STRUCTURED_SOURCE, "degraded": True}
        serializer = RagResponseSerializer({"answer": "答案", "sources": [source], "success": True})

        self.assertTrue(serializer.data["sources"][0]["degraded"])

    def test_score_optional_passes_through(self):
        """R-UX-04：score 透传且可选——带分数来源透出，无分数来源不出现该键。"""
        scored = RagResponseSerializer(
            {"answer": "答案", "sources": [{**self._STRUCTURED_SOURCE, "score": 0.91}], "success": True}
        )
        unscored = RagResponseSerializer({"answer": "答案", "sources": [self._STRUCTURED_SOURCE], "success": True})

        self.assertEqual(scored.data["sources"][0]["score"], 0.91)
        self.assertNotIn("score", unscored.data["sources"][0])

    def test_empty_and_missing_sources(self):
        """空列表与缺省 sources 均合法（不抛 ValidationError）。"""
        empty = RagResponseSerializer({"answer": "答案", "sources": [], "success": True})
        missing = RagResponseSerializer({"answer": "答案", "success": True})

        self.assertEqual(empty.data["sources"], [])
        self.assertEqual(missing.data["sources"], [])

    def test_non_dict_source_rejected(self):
        """非结构化来源（repr 化字符串等）被拒绝——固化结构化契约，防止回归。"""
        with self.assertRaises(ValidationError):
            RagResponseSerializer(
                data={
                    "answer": "答案",
                    "sources": [str(self._STRUCTURED_SOURCE)],
                    "success": True,
                }
            ).is_valid(raise_exception=True)

    def test_metadata_dict_required(self):
        """metadata 必须是 dict（白名单化后的来源 metadata）。"""
        bad = {**self._STRUCTURED_SOURCE, "metadata": "not-a-dict"}
        with self.assertRaises(ValidationError):
            RagResponseSerializer(
                data={"answer": "答案", "sources": [bad], "success": True}
            ).is_valid(raise_exception=True)


if __name__ == "__main__":
    unittest.main()
