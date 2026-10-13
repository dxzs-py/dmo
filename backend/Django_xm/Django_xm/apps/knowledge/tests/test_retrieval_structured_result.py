"""UnifiedRagPipeline 结构化检索结果与工具判空测试（lc06 spec Task 4 / B2）。

修复前缺口：
- search_with_retriever 吞一切异常返回文案"检索过程中发生错误，请稍后重试。"，
  retriever_tool 三处以 `"未找到" in result` 子串判定空结果——文档正文含"未找到"
  即误判为空，反向更糟：错误文案不含关键词时被加"【真实检索到的知识库内容】"
  前缀冒充知识回灌 LLM。

修复契约（本文件锁定）：
1. PipelineSearchResult（status: ok/empty/error）承载状态，调用方只读 status；
2. retriever_tool 按 status 分流：ok 加 KB_RESULT_INSTRUCTION 前缀（真实知识）、
   empty 返回受控空结果文案（不冒充知识）、error 返回"知识库检索失败: ..."；
3. 错误/空结果路径绝不携带"真实检索内容"前缀。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.knowledge.tests.test_retrieval_structured_result
（纯单元测试，无 DB / Redis / LLM 依赖）
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.rag_retrieval import PipelineSearchResult, UnifiedRagPipeline
from Django_xm.apps.knowledge.services.retriever_tool import (
    KB_RESULT_INSTRUCTION,
    create_retriever_tool,
)

_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"

_QUERY = "未找到订单怎么办"


def _doc(content="相关内容"):
    return Document(page_content=content, metadata={"file_name": "a.md"})


class PipelineSearchResultContractTests(unittest.TestCase):
    """search_with_retriever 结构化返回契约（真实 Pipeline + retriever 桩）。"""

    def test_ok_when_retriever_hits(self):
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = mock.MagicMock()
        retriever.invoke.return_value = [_doc("LangChain 是框架")]

        result = pipeline.search_with_retriever(retriever=retriever, query=_QUERY)

        self.assertIsInstance(result, PipelineSearchResult)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.is_ok)
        self.assertIn("LangChain 是框架", result.text)
        self.assertEqual(len(result.docs), 1)
        self.assertEqual(result.degradation_level, 0)
        self.assertIsNone(result.error)

    def test_empty_when_retriever_returns_nothing(self):
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = mock.MagicMock()
        retriever.invoke.return_value = []

        result = pipeline.search_with_retriever(retriever=retriever, query=_QUERY)

        self.assertEqual(result.status, "empty")
        self.assertEqual(result.text, "知识库中未找到与您问题相关的信息。")
        self.assertEqual(result.docs, [])

    def test_empty_attachment_scenario_text(self):
        pipeline = UnifiedRagPipeline(scenario="attachment")
        retriever = mock.MagicMock()
        retriever.invoke.return_value = []

        result = pipeline.search_with_retriever(retriever=retriever, query=_QUERY)

        self.assertEqual(result.status, "empty")
        self.assertEqual(result.text, "当前会话无可用附件，且未检索到相关内容。")

    def test_error_is_structured_not_text(self):
        """检索异常：status=error 结构化返回（不再以文案字符串承载错误）。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = mock.MagicMock()
        retriever.invoke.side_effect = RuntimeError("db down")

        result = pipeline.search_with_retriever(retriever=retriever, query=_QUERY)

        self.assertEqual(result.status, "error")
        self.assertIn("db down", result.error)
        self.assertEqual(result.docs, [])


def _make_tool(retrieval_mode="precise"):
    # retriever 传 None：pipeline 在各用例中被整体替换，真实检索器不参与
    # （MagicMock 无法通过 pydantic 的 BaseRetriever | None 校验）
    return create_retriever_tool(
        None,
        name="knowledge_base",
        retrieval_mode=retrieval_mode,
    )


def _patch_pipeline_return(pipeline_result):
    """patch rag_retrieval.UnifiedRagPipeline：search_with_retriever 返回指定结构化结果"""
    pipeline_cls = mock.MagicMock()
    pipeline_cls.return_value.search_with_retriever.return_value = pipeline_result
    return mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls)


class RetrieverToolStatusDrivenTests(unittest.TestCase):
    """retriever_tool：按 status 分流，禁止正文子串判空与错误冒充知识。"""

    def test_ok_text_containing_bu_zhaodao_not_misjudged(self):
        """文档正文含"未找到"不误判：ok 状态照常加真实知识前缀（修复前子串误判为空）。"""
        fake_text = "订单模块说明：用户问未找到订单怎么办时，应检查筛选条件。"
        result = PipelineSearchResult(status="ok", text=fake_text, docs=[_doc(fake_text)])
        tool = _make_tool()

        with _patch_pipeline_return(result):
            output = tool._run_precise(_QUERY)

        self.assertIn(KB_RESULT_INSTRUCTION, output)
        self.assertIn(fake_text, output)

    def test_error_not_masquerading_as_knowledge(self):
        """pipeline 错误：返回明确错误信息，不带"真实检索内容"前缀。"""
        result = PipelineSearchResult(
            status="error", text="检索过程中发生错误，请稍后重试。", error="connection refused"
        )
        tool = _make_tool()

        with _patch_pipeline_return(result):
            output = tool._run_precise(_QUERY)

        self.assertNotIn(KB_RESULT_INSTRUCTION, output)
        self.assertIn("知识库检索失败", output)
        self.assertIn("connection refused", output)

    def test_empty_returns_controlled_text_without_knowledge_prefix(self):
        """空结果：受控文案原样返回，不加"真实检索内容"前缀。"""
        result = PipelineSearchResult(status="empty", text="知识库中未找到与您问题相关的信息。")
        tool = _make_tool()

        with _patch_pipeline_return(result):
            output = tool._run_precise(_QUERY)

        self.assertNotIn(KB_RESULT_INSTRUCTION, output)
        self.assertEqual(output, "知识库中未找到与您问题相关的信息。")

    def test_comprehensive_status_driven(self):
        """comprehensive 模式同样按 status 分流（错误不冒充知识）。"""
        result = PipelineSearchResult(status="error", text="检索过程中发生错误，请稍后重试。", error="boom")
        tool = _make_tool(retrieval_mode="comprehensive")

        with _patch_pipeline_return(result):
            output = tool._run_comprehensive(_QUERY)

        self.assertNotIn(KB_RESULT_INSTRUCTION, output)
        self.assertIn("知识库检索失败", output)

    def test_comprehensive_ok_with_bu_zhaodao_content(self):
        """comprehensive 模式：正文含"未找到"不误判（修复前子串判空）。"""
        fake_text = "全局说明：未找到相关记录时请联系管理员。"
        result = PipelineSearchResult(status="ok", text=fake_text)
        tool = _make_tool(retrieval_mode="comprehensive")

        with _patch_pipeline_return(result):
            output = tool._run_comprehensive(_QUERY)

        self.assertIn(KB_RESULT_INSTRUCTION, output)
        self.assertIn(fake_text, output)


class RetrieverToolAsyncTests(unittest.IsolatedAsyncioTestCase):
    """_arun_comprehensive：异步路径 status 分流 + 重 IO 离开事件循环。"""

    async def test_arun_comprehensive_error_status(self):
        result = PipelineSearchResult(status="error", text="检索过程中发生错误，请稍后重试。", error="timeout")
        tool = _make_tool(retrieval_mode="comprehensive")

        with _patch_pipeline_return(result):
            output = await tool._arun_comprehensive(_QUERY)

        self.assertNotIn(KB_RESULT_INSTRUCTION, output)
        self.assertIn("知识库检索失败", output)
        self.assertIn("timeout", output)

    async def test_arun_comprehensive_runs_off_event_loop(self):
        """RP-03 同构：异步 comprehensive 检索经 to_thread 在工作线程执行。"""
        import threading

        main_thread = threading.current_thread()
        observed = {}

        pipeline_cls = mock.MagicMock()

        def _probe(*args, **kwargs):
            observed["thread"] = threading.current_thread()
            return PipelineSearchResult(status="ok", text="内容")

        pipeline_cls.return_value.search_with_retriever.side_effect = _probe
        tool = _make_tool(retrieval_mode="comprehensive")

        with mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls):
            output = await tool._arun_comprehensive(_QUERY)

        self.assertIsNot(observed["thread"], main_thread)
        self.assertIn("内容", output)


if __name__ == "__main__":
    unittest.main()
