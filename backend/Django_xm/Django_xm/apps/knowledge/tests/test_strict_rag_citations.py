"""strict_rag 引用体系测试（lc06 spec Task 4 / B1）。

修复前缺口：prompt 上下文按 chunk 序编号、sources 按来源文件去重，同一文件
多个 chunk 时答案中的 [n] 与 sources 错位；且全程无代码校验答案中的 [n]
是否越界（模型可编造编号）。

修复契约（本文件锁定）：
1. 编号与 sources 同源生成（_group_docs_by_source 分组）：一个来源文件一个
   编号，prompt 标记 [i] 与 sources[i-1] 一一对应（query/stream/astream 三路一致）；
2. 生成后校验：越界 [n] 剔除（同步答案整体替换 _strip_invalid_citations；
   流式经 _StreamingCitationFilter 增量过滤，支持 [n] 跨 chunk 分片）；
3. sources 元数据仍走 sanitize_source_metadata 白名单。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.knowledge.tests.test_strict_rag_citations
（纯单元测试，无 DB / Redis / LLM 依赖）
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.strict_rag_chain import (
    NO_RESULT_ANSWER,
    _format_docs,
    _group_docs_by_source,
    _StreamingCitationFilter,
    _strip_invalid_citations,
    astream_strict_rag,
    query_strict_rag,
    stream_strict_rag,
)
from Django_xm.common.testing import create_fake_llm

_STRICT_RAG = "Django_xm.apps.knowledge.services.strict_rag_chain"
_KEYWORD_FALLBACK = "Django_xm.apps.knowledge.services.keyword_fallback"
_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"

_QUERY = "什么是 LangChain"
_K = 4


def _doc(content, file_name, **extra):
    return Document(page_content=content, metadata={"file_name": file_name, **extra})


class GroupDocsBySourceTests(unittest.TestCase):
    """_group_docs_by_source：同源分组语义（编号权威源）。"""

    def test_same_file_chunks_grouped_in_first_seen_order(self):
        docs = [
            _doc("块1", "a.pdf"),
            _doc("块2", "b.pdf"),
            _doc("块3", "a.pdf"),
        ]
        groups = _group_docs_by_source(docs)
        self.assertEqual([[d.page_content for d in g] for g in groups], [["块1", "块3"], ["块2"]])

    def test_empty_docs(self):
        self.assertEqual(_group_docs_by_source([]), [])


class FormatDocsNumberingTests(unittest.TestCase):
    """_format_docs：同一文件多个 chunk 共用一个编号。"""

    def test_same_file_shares_one_number(self):
        docs = [_doc("块1内容", "a.pdf"), _doc("块2内容", "a.pdf"), _doc("乙内容", "b.pdf")]
        text = _format_docs(docs)
        self.assertIn("参考资料 [1] 来源: a.pdf ---\n块1内容", text)
        self.assertIn("参考资料 [1] 来源: a.pdf (续) ---\n块2内容", text)
        self.assertIn("参考资料 [2] 来源: b.pdf ---\n乙内容", text)
        self.assertNotIn("[3]", text)


class StripInvalidCitationsTests(unittest.TestCase):
    """_strip_invalid_citations：越界 [n] 剔除（同步答案）。"""

    def test_valid_citations_kept(self):
        self.assertEqual(_strip_invalid_citations("依据[1]与[2]得出", 2), "依据[1]与[2]得出")

    def test_out_of_range_stripped(self):
        self.assertEqual(_strip_invalid_citations("依据[1]与[9]得出", 1), "依据[1]与得出")

    def test_zero_stripped(self):
        self.assertEqual(_strip_invalid_citations("引用[0]无效", 3), "引用无效")

    def test_plain_brackets_untouched(self):
        self.assertEqual(_strip_invalid_citations("见[注]说明", 0), "见[注]说明")


class StreamingCitationFilterTests(unittest.TestCase):
    """_StreamingCitationFilter：流式越界引用剔除（含跨 chunk 分片）。"""

    def test_valid_citation_passes(self):
        f = _StreamingCitationFilter(source_count=2)
        out = f.feed("依据[1]和[2]") + f.flush()
        self.assertEqual(out, "依据[1]和[2]")

    def test_invalid_citation_dropped(self):
        f = _StreamingCitationFilter(source_count=1)
        out = f.feed("依据[1]和[9]") + f.flush()
        self.assertEqual(out, "依据[1]和")

    def test_citation_split_across_chunks(self):
        f = _StreamingCitationFilter(source_count=1)
        out = f.feed("依据[") + f.feed("1") + f.feed("]有效[") + f.feed("9") + f.feed("]") + f.flush()
        self.assertEqual(out, "依据[1]有效")

    def test_plain_bracket_text_preserved(self):
        f = _StreamingCitationFilter(source_count=1)
        out = f.feed("见[注]与[abc]说明") + f.flush()
        self.assertEqual(out, "见[注]与[abc]说明")

    def test_unclosed_tail_flushed_as_text(self):
        f = _StreamingCitationFilter(source_count=2)
        out = f.feed("结尾[12") + f.flush()
        self.assertEqual(out, "结尾[12")

    def test_long_digit_tail_not_held(self):
        # 超过持有上限的数字串按普通文本处理（不阻塞流）
        f = _StreamingCitationFilter(source_count=2)
        out = f.feed("x[123456789012345") + f.flush()
        self.assertEqual(out, "x[123456789012345")


class QueryCitationAlignmentTests(unittest.TestCase):
    """query_strict_rag：多 chunk 同文件 sources 对齐 + 越界引用剔除（集成）。"""

    def _run(self, docs, llm_pieces):
        pipeline_cls = mock.MagicMock()
        pipeline_cls.return_value.retrieve_documents_from_retriever.return_value = list(docs)
        fake_llm = create_fake_llm(pieces=llm_pieces)
        with (
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls),
            mock.patch(f"{_KEYWORD_FALLBACK}.keyword_search_fallback", return_value=[]),
            mock.patch(f"{_KEYWORD_FALLBACK}.record_degradation_metric"),
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
        ):
            result = query_strict_rag(mock.MagicMock(), _QUERY, k=_K, use_hyde=False, collection_name="c")
        return result, fake_llm

    def test_sources_align_with_prompt_numbering(self):
        """同一文件多个 chunk：sources 仅一条，prompt 中两 chunk 共用编号 [1]。"""
        docs = [_doc("甲文件块1", "a.pdf"), _doc("甲文件块2", "a.pdf"), _doc("乙文件块", "b.pdf")]
        result, fake_llm = self._run(docs, ("ok",))

        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["sources"][0]["source"], "a.pdf")
        self.assertEqual(result["sources"][1]["source"], "b.pdf")
        prompt = fake_llm.invoke_messages[0].content
        self.assertIn("参考资料 [1] 来源: a.pdf", prompt)
        self.assertIn("参考资料 [1] 来源: a.pdf (续)", prompt)
        self.assertIn("参考资料 [2] 来源: b.pdf", prompt)

    def test_out_of_range_citation_stripped(self):
        """答案中越界 [n] 被剔除，有效 [n] 保留（n 与 sources 一一对应）。"""
        docs = [_doc("甲内容", "a.pdf"), _doc("乙内容", "b.pdf")]
        result, _ = self._run(docs, ("依据[1]和[2]，另有[3]与[0]无效",))

        self.assertEqual(result["answer"], "依据[1]和[2]，另有与无效")
        self.assertEqual(len(result["sources"]), 2)


class StreamCitationFilteringTests(unittest.TestCase):
    """stream_strict_rag：流式路径越界引用在发出前剔除。"""

    def _collect(self, docs, llm_pieces):
        pipeline_cls = mock.MagicMock()
        pipeline_cls.return_value.retrieve_documents_from_retriever.return_value = list(docs)
        fake_llm = create_fake_llm(pieces=llm_pieces)
        with (
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls),
            mock.patch(f"{_KEYWORD_FALLBACK}.keyword_search_fallback", return_value=[]),
            mock.patch(f"{_KEYWORD_FALLBACK}.record_degradation_metric"),
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
        ):
            return list(
                stream_strict_rag(mock.MagicMock(), _QUERY, k=_K, use_hyde=False, collection_name="c")
            )

    def test_stream_invalid_citation_dropped_before_yield(self):
        """[9] 越界：不出现在任何 chunk 事件中；[1] 有效保留。"""
        docs = [_doc("甲内容", "a.pdf")]
        events = self._collect(docs, ("依据[", "1", "]和[", "9", "]结尾"))

        chunk_text = "".join(e["content"] for e in events if e["type"] == "chunk")
        self.assertEqual(chunk_text, "依据[1]和结尾")

    def test_stream_sources_alignment(self):
        """多 chunk 同文件：sources 事件仅一条（与正文编号同源）。"""
        docs = [_doc("甲1", "a.pdf"), _doc("甲2", "a.pdf"), _doc("乙", "b.pdf")]
        events = self._collect(docs, ("[1][2]",))

        sources_events = [e for e in events if e["type"] == "sources"]
        self.assertEqual(len(sources_events), 1)
        self.assertEqual([s["source"] for s in sources_events[0]["data"]], ["a.pdf", "b.pdf"])


class AstreamCitationFilteringTests(unittest.IsolatedAsyncioTestCase):
    """astream_strict_rag：异步流式路径引用过滤与三路一致。"""

    async def test_astream_invalid_citation_dropped(self):
        pipeline_cls = mock.MagicMock()
        pipeline_cls.return_value.retrieve_documents_from_retriever.return_value = [_doc("甲内容", "a.pdf")]
        fake_llm = create_fake_llm(pieces=("依据[", "1", "]和[", "9", "]结尾"))
        with (
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls),
            mock.patch(f"{_KEYWORD_FALLBACK}.keyword_search_fallback", return_value=[]),
            mock.patch(f"{_KEYWORD_FALLBACK}.record_degradation_metric"),
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
        ):
            events = []
            async for event in astream_strict_rag(
                mock.MagicMock(), _QUERY, k=_K, use_hyde=False, collection_name="c"
            ):
                events.append(event)

        chunk_text = "".join(e["content"] for e in events if e["type"] == "chunk")
        self.assertEqual(chunk_text, "依据[1]和结尾")


class EmptyResultGuardTests(unittest.TestCase):
    """空结果受控应答常量契约（B3：受控文案，非 LLM 幻觉）。"""

    def test_no_result_answer_is_controlled_text(self):
        self.assertEqual(NO_RESULT_ANSWER, "根据知识库中的资料，未找到与您问题相关的信息。")


if __name__ == "__main__":
    unittest.main()
