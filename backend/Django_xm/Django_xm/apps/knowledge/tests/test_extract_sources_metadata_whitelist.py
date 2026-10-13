"""C-7（信息泄漏）：strict_rag_chain._extract_sources metadata 白名单测试。

缺陷（修复前）：_extract_sources 将 doc.metadata 原样放入每条 source，chunk
metadata 含 source=<data_uploads 服务器绝对路径>（document_service.load_document
注入），流式/REST sources 事件向客户端暴露服务器目录结构。

修复契约（本文件锁定）：
1. 顶层 source：file_name 优先 → source 路径 basename 回退 → filename → "未知来源"，
   任何路径分隔符不得外泄；
2. metadata 按白名单过滤（file_name/file_type/doc_type/page/page_label/total_pages/
   original_name/degraded），服务器路径键 source 一律剥离；
3. degraded 标志照常透传（顶层 + metadata）；
4. 同名去重语义保持。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_extract_sources_metadata_whitelist --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.apps.knowledge.services.strict_rag_chain import _extract_sources


def _doc(content: str, metadata: dict):
    from langchain_core.documents import Document

    return Document(page_content=content, metadata=metadata)


_WIN_ABS_SOURCE = "D:\\data\\uploads\\user_1_kb\\年度报告.pdf"


class ExtractSourcesMetadataWhitelistTests(SimpleTestCase):
    def test_server_absolute_path_stripped(self):
        """顶层 source 与 metadata 均不得含服务器绝对路径。"""
        docs = [
            _doc(
                "正文内容",
                {
                    "source": _WIN_ABS_SOURCE,
                    "file_name": "年度报告.pdf",
                    "file_type": ".pdf",
                    "doc_type": "pdf",
                    "page": 3,
                },
            )
        ]
        (source,) = _extract_sources(docs)
        self.assertEqual(source["source"], "年度报告.pdf")
        self.assertNotIn("D:\\", source["source"])
        self.assertNotIn("source", source["metadata"])
        self.assertEqual(
            source["metadata"],
            {"file_name": "年度报告.pdf", "file_type": ".pdf", "doc_type": "pdf", "page": 3},
        )

    def test_degraded_flag_preserved(self):
        """降级标记照常透传（顶层 + metadata）。"""
        docs = [
            _doc(
                "降级内容",
                {
                    "source": _WIN_ABS_SOURCE,
                    "file_name": "a.pdf",
                    "degraded": True,
                },
            )
        ]
        (source,) = _extract_sources(docs)
        self.assertTrue(source["degraded"])
        self.assertTrue(source["metadata"]["degraded"])

    def test_fallback_to_basename_without_file_name(self):
        """无 file_name 时回退 source 路径 basename（仍不泄露目录）。"""
        docs = [_doc("内容", {"source": "/srv/uploads/user_1_kb/manual.docx"})]
        (source,) = _extract_sources(docs)
        self.assertEqual(source["source"], "manual.docx")

    def test_fallback_chain_ends_at_unknown(self):
        """无 source/file_name → filename → "未知来源"。"""
        (source_with_filename,) = _extract_sources([_doc("a", {"filename": "f.txt"})])
        self.assertEqual(source_with_filename["source"], "f.txt")
        (source_unknown,) = _extract_sources([_doc("b", {"foo": "bar"})])
        self.assertEqual(source_unknown["source"], "未知来源")

    def test_dedup_by_display_name_kept(self):
        """同一 file_name 的多个 chunk 仅出一条（去重语义保持）。"""
        docs = [
            _doc("块1", {"source": _WIN_ABS_SOURCE, "file_name": "年度报告.pdf", "page": 1}),
            _doc("块2", {"source": _WIN_ABS_SOURCE, "file_name": "年度报告.pdf", "page": 2}),
            _doc("块3", {"source": _WIN_ABS_SOURCE, "file_name": "另一份.pdf", "page": 1}),
        ]
        sources = _extract_sources(docs)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["source"], "年度报告.pdf")
        self.assertEqual(sources[1]["source"], "另一份.pdf")

    def test_content_truncation_kept(self):
        """content 截断语义保持（200 字符）。"""
        docs = [_doc("x" * 300, {"file_name": "long.txt"})]
        (source,) = _extract_sources(docs)
        self.assertEqual(len(source["content"]), 203)


class ExtractSourcesScoreTests(SimpleTestCase):
    """R-UX-04：检索内核写入 metadata["score"] 的相关度分数在顶层透出。"""

    def test_score_promoted_to_top_level(self):
        """metadata["score"]（C1/KV-2 归一相似度语义）顶层透出为 float。"""
        docs = [_doc("正文", {"file_name": "a.md", "score": 0.87})]
        (source,) = _extract_sources(docs)
        self.assertEqual(source["score"], 0.87)
        self.assertIsInstance(source["score"], float)
        # metadata 白名单不含 score（避免双份携带）
        self.assertNotIn("score", source["metadata"])

    def test_no_score_key_absent(self):
        """无分数出口的检索路径（MMR/重排/关键词降级）不带 score 键。"""
        (source,) = _extract_sources([_doc("内容", {"file_name": "a.md"})])
        self.assertNotIn("score", source)

    def test_non_numeric_score_ignored(self):
        """非数值 score（脏数据）不透出。"""
        (source,) = _extract_sources([_doc("内容", {"file_name": "a.md", "score": "high"})])
        self.assertNotIn("score", source)


class ExtractSourcesRerankScoreTests(SimpleTestCase):
    """3.6 复核修复：rerank 开启时 FlashRank 的 relevance_score 兜底透出。

    实测链路（.trae/tmp/verify_3_6_rerank_score.py）：rerank 开启时检索器为
    ContextualCompressionRetriever，with_scores 鸭子判定不命中 →
    metadata["score"] 不写入；FlashRank 以 relevance_score（numpy.float32）写入。
    """

    def test_relevance_score_numpy_float32_promoted(self):
        """np.float32 的 relevance_score 经 float() 归一透出（isinstance 判定会漏）。"""
        import numpy as np

        (source,) = _extract_sources([_doc("正文", {"file_name": "a.md", "relevance_score": np.float32(0.42)})])
        self.assertIn("score", source)
        self.assertEqual(source["score"], float(np.float32(0.42)))
        self.assertIsInstance(source["score"], float)

    def test_relevance_score_python_float_promoted(self):
        """普通 float 的 relevance_score 同样透出。"""
        (source,) = _extract_sources([_doc("正文", {"file_name": "a.md", "relevance_score": 0.66})])
        self.assertEqual(source["score"], 0.66)

    def test_search_score_takes_precedence(self):
        """score 与 relevance_score 并存时优先检索阶段分数（C1/KV-2 语义）。"""
        (source,) = _extract_sources(
            [_doc("正文", {"file_name": "a.md", "score": 0.9, "relevance_score": 0.1})]
        )
        self.assertEqual(source["score"], 0.9)

    def test_non_numeric_relevance_score_ignored(self):
        """非数值 relevance_score（脏数据）不透出。"""
        (source,) = _extract_sources([_doc("内容", {"file_name": "a.md", "relevance_score": "bad"})])
        self.assertNotIn("score", source)
