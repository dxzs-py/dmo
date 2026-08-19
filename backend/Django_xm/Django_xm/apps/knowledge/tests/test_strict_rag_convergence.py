"""strict_rag_chain 收敛后行为等价性测试（lc06 spec Task 4）。

覆盖 Django_xm.apps.knowledge.services.strict_rag_chain 三个在役入口：
- query_strict_rag（同步字典）：正常路径 / embedding 降级（fallback 收到原始 query
  与 k 关键字参数）/ 非 embedding 异常上抛 / 空结果走知识库降级管道 / 空结果且
  知识库为空 / 步骤 1 已降级且步骤 2 未命中时 degraded 仍为 True（degraded or
  used_kb 语义）/ HyDE 改写仅用于检索、prompt 用原始 query / use_hyde=False 跳过改写
- stream_strict_rag（同步生成器）：事件序列（heartbeat 检索 → heartbeat 生成 →
  chunk → sources）/ 文档自带 degraded 标记触发 degradation 事件 / 空结果分支
  （chunk + degradation 后终止，无 sources）/ use_hyde 时首个事件为改写 heartbeat
- astream_strict_rag（异步生成器）：事件序列 / embedding 降级（fallback 经
  to_thread 位置传参 (query, collection_name, k)，与 sync 的关键字传参差异锁定）/
  空结果分支（load_kb_documents 位置传参）
- 死符号契约：模块与 services 包不再导出 aquery_strict_rag /
  create_strict_rag_chain / STRICT_RAG_SYSTEM_PROMPT / _get_chat_model /
  STRICT_RAG_QA_PROMPT（re-export）

mock 边界（与 test_retrieval_fallback.py 同风格，patch 源模块属性，函数内延迟
导入在调用时解析）：
- rag_retrieval.UnifiedRagPipeline / rag_retrieval.load_kb_documents
- retrieval_service._keyword_search_fallback / _record_degradation_metric
  （_is_embedding_error 用真实实现：ConnectionError 命中降级、ValueError 不命中）
- strict_rag_chain._resolve_chat_model / _hyde_rewrite_query(_sync)

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.knowledge.tests.test_strict_rag_convergence
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
    KB_DEGRADATION_NOTICE,
    KEYWORD_DEGRADATION_NOTICE,
    NO_RESULT_ANSWER,
    astream_strict_rag,
    query_strict_rag,
    stream_strict_rag,
)

_STRICT_RAG = "Django_xm.apps.knowledge.services.strict_rag_chain"
_RETRIEVAL_SERVICE = "Django_xm.apps.knowledge.services.retrieval_service"
_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"

_QUERY = "什么是 LangChain"
_COLLECTION = "test_collection"
_K = 6


class _FakeChunk:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    """invoke / stream / astream 三协议替身，记录收到的 messages"""

    def __init__(self, pieces=("回答甲", "回答乙")):
        self.pieces = list(pieces)
        self.invoke_messages = None
        self.stream_messages = None
        self.astream_messages = None

    def invoke(self, messages):
        self.invoke_messages = messages
        return _FakeChunk("".join(self.pieces))

    def stream(self, messages):
        self.stream_messages = messages
        for piece in self.pieces:
            yield _FakeChunk(piece)

    async def astream(self, messages):
        self.astream_messages = messages
        for piece in self.pieces:
            yield _FakeChunk(piece)


def _make_pipeline(retrieve_docs=None, retrieve_exc=None, search_return="管道检索回答"):
    """构造 UnifiedRagPipeline 替身：初始检索返回值 / 抛出异常 / search 返回值可控"""
    pipeline_cls = mock.MagicMock()
    instance = pipeline_cls.return_value
    if retrieve_exc is not None:
        instance.retrieve_documents_from_retriever.side_effect = retrieve_exc
    else:
        instance.retrieve_documents_from_retriever.return_value = list(retrieve_docs or [])
    instance.search.return_value = search_return
    return pipeline_cls, instance


def _normal_doc():
    return Document(page_content="LangChain 是 LLM 应用开发框架", metadata={"source": "a.md"})


def _degraded_doc():
    return Document(page_content="降级检索内容", metadata={"degraded": True, "source": "kb.md"})


class QueryStrictRagTests(unittest.TestCase):
    """query_strict_rag：同步字典入口各分支。"""

    def _run(
        self,
        *,
        retrieve_docs=None,
        retrieve_exc=None,
        fallback_docs=None,
        kb_docs=None,
        kb_tokens=0,
        use_hyde=False,
    ):
        pipeline_cls, instance = _make_pipeline(retrieve_docs=retrieve_docs, retrieve_exc=retrieve_exc)
        fake_llm = _FakeLLM()
        hyde_sync = mock.MagicMock(return_value="改写后的假设文档")
        with (
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls),
            mock.patch(f"{_RAG_RETRIEVAL}.load_kb_documents", return_value=(list(kb_docs or []), kb_tokens)),
            mock.patch(
                f"{_RETRIEVAL_SERVICE}._keyword_search_fallback",
                return_value=list(fallback_docs or []),
            ) as fallback,
            mock.patch(f"{_RETRIEVAL_SERVICE}._record_degradation_metric") as record_metric,
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
            mock.patch(f"{_STRICT_RAG}._hyde_rewrite_query_sync", hyde_sync),
        ):
            result = query_strict_rag(
                mock.MagicMock(), _QUERY, k=_K, use_hyde=use_hyde, collection_name=_COLLECTION
            )
        return result, instance, fallback, record_metric, fake_llm, hyde_sync

    def test_normal_path(self):
        """正常路径：answer/sources/retrieved_docs/success，无 degraded 键。"""
        doc = _normal_doc()
        result, instance, fallback, _, fake_llm, hyde_sync = self._run(retrieve_docs=[doc])

        self.assertEqual(
            result,
            {"answer": "回答甲回答乙", "sources": result["sources"], "retrieved_docs": [doc], "success": True},
        )
        self.assertNotIn("degraded", result)
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["source"], "a.md")
        # prompt 注入文档内容与原始查询
        prompt_text = fake_llm.invoke_messages[0].content
        self.assertIn("LangChain 是 LLM 应用开发框架", prompt_text)
        self.assertIn(_QUERY, prompt_text)
        # 初始检索经 Pipeline，未走降级
        instance.retrieve_documents_from_retriever.assert_called_once()
        fallback.assert_not_called()
        hyde_sync.assert_not_called()

    def test_embedding_error_falls_back_with_original_query(self):
        """embedding 故障：降级关键词检索收到原始 query（非改写查询）+ collection + k 关键字参数。"""
        doc = _degraded_doc()
        result, _, fallback, record_metric, _, _ = self._run(
            retrieve_exc=ConnectionError("Connection refused"), fallback_docs=[doc]
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["retrieved_docs"], [doc])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degradation_notice"], KEYWORD_DEGRADATION_NOTICE)
        # sync 版降级调用为关键字参数 k（与原实现逐字一致）
        fallback.assert_called_once_with(_QUERY, _COLLECTION, k=_K)
        record_metric.assert_called_once()
        self.assertEqual(record_metric.call_args.args[0], _COLLECTION)

    def test_non_embedding_error_propagates(self):
        """非 embedding 异常：原样上抛，不触发降级。"""
        with self.assertRaises(ValueError):
            self._run(retrieve_exc=ValueError("业务参数错误"))

    def test_empty_docs_uses_kb_pipeline(self):
        """空结果：走知识库全量降级管道，answer 来自 pipeline.search，degraded=True。"""
        kb_docs = [_normal_doc()]
        result, instance, _, _, _, _ = self._run(retrieve_docs=[], kb_docs=kb_docs, kb_tokens=42)

        self.assertEqual(result["answer"], "管道检索回答")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["retrieved_docs"], [])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degradation_notice"], KB_DEGRADATION_NOTICE)
        instance.search.assert_called_once_with(_QUERY, kb_docs, 42)

    def test_empty_docs_without_kb_returns_default_answer(self):
        """空结果且知识库无文档：默认未找到回答，不带 degraded。"""
        result, _, _, _, _, _ = self._run(retrieve_docs=[], kb_docs=[])

        self.assertEqual(result["answer"], NO_RESULT_ANSWER)
        self.assertEqual(result["sources"], [])
        self.assertNotIn("degraded", result)

    def test_degraded_retrieval_with_empty_fallback_keeps_degraded(self):
        """步骤 1 降级且降级结果为空、知识库也无文档：degraded 仍为 True（degraded or used_kb 语义）。"""
        result, _, fallback, _, _, _ = self._run(
            retrieve_exc=ConnectionError("Connection refused"), fallback_docs=[], kb_docs=[]
        )

        fallback.assert_called_once()
        self.assertEqual(result["answer"], NO_RESULT_ANSWER)
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degradation_notice"], KB_DEGRADATION_NOTICE)

    def test_hyde_rewrites_retrieval_only(self):
        """HyDE 改写仅用于向量检索；prompt 与降级路径使用原始 query。"""
        result, instance, _, _, fake_llm, hyde_sync = self._run(retrieve_docs=[_normal_doc()], use_hyde=True)

        hyde_sync.assert_called_once_with(_QUERY, llm=None)
        # 检索使用改写后查询
        retrieve_kwargs = instance.retrieve_documents_from_retriever.call_args.kwargs
        self.assertEqual(retrieve_kwargs["query"], "改写后的假设文档")
        # prompt 使用原始查询
        self.assertIn(_QUERY, fake_llm.invoke_messages[0].content)
        self.assertNotIn("改写后的假设文档", fake_llm.invoke_messages[0].content)
        self.assertNotIn("degraded", result)

    def test_hyde_disabled_skips_rewrite(self):
        """use_hyde=False：不调用改写，检索直接用原始 query。"""
        _, instance, _, _, _, hyde_sync = self._run(retrieve_docs=[_normal_doc()], use_hyde=False)

        hyde_sync.assert_not_called()
        retrieve_kwargs = instance.retrieve_documents_from_retriever.call_args.kwargs
        self.assertEqual(retrieve_kwargs["query"], _QUERY)


class StreamStrictRagTests(unittest.TestCase):
    """stream_strict_rag：同步生成器事件序列。"""

    def _collect(self, *, retrieve_docs=None, retrieve_exc=None, kb_docs=None, kb_tokens=0, use_hyde=False):
        pipeline_cls, instance = _make_pipeline(retrieve_docs=retrieve_docs, retrieve_exc=retrieve_exc)
        fake_llm = _FakeLLM()
        hyde_sync = mock.MagicMock(return_value="改写后的假设文档")
        with (
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls),
            mock.patch(f"{_RAG_RETRIEVAL}.load_kb_documents", return_value=(list(kb_docs or []), kb_tokens)),
            mock.patch(f"{_RETRIEVAL_SERVICE}._keyword_search_fallback", return_value=[]),
            mock.patch(f"{_RETRIEVAL_SERVICE}._record_degradation_metric"),
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
            mock.patch(f"{_STRICT_RAG}._hyde_rewrite_query_sync", hyde_sync),
        ):
            events = list(
                stream_strict_rag(
                    mock.MagicMock(), _QUERY, k=_K, use_hyde=use_hyde, collection_name=_COLLECTION
                )
            )
        return events, instance, fake_llm, hyde_sync

    def test_normal_event_sequence(self):
        """正常路径事件序列：heartbeat(检索) → heartbeat(生成) → chunk ×2 → sources。"""
        events, _, _, _ = self._collect(retrieve_docs=[_normal_doc()])

        self.assertEqual(
            [e["type"] for e in events],
            ["heartbeat", "heartbeat", "chunk", "chunk", "sources"],
        )
        self.assertEqual(events[0]["message"], "正在检索文档...")
        self.assertEqual(events[1]["message"], "正在生成回答...")
        self.assertEqual([e["content"] for e in events[2:4]], ["回答甲", "回答乙"])
        self.assertEqual(len(events[4]["data"]), 1)

    def test_degraded_doc_triggers_degradation_event(self):
        """文档自带 degraded 标记：chunk 后发 degradation 事件（KEYWORD 提示）。"""
        events, _, _, _ = self._collect(retrieve_docs=[_degraded_doc()])

        types = [e["type"] for e in events]
        self.assertEqual(types, ["heartbeat", "heartbeat", "chunk", "chunk", "degradation", "sources"])
        degradation_events = [e for e in events if e["type"] == "degradation"]
        self.assertEqual(degradation_events[0]["message"], KEYWORD_DEGRADATION_NOTICE)

    def test_empty_result_branch_events(self):
        """空结果分支：chunk(降级文本) + degradation(KB 提示) 后终止，无 sources。"""
        events, _, _, _ = self._collect(retrieve_docs=[], kb_docs=[_normal_doc()], kb_tokens=10)

        self.assertEqual([e["type"] for e in events], ["heartbeat", "chunk", "degradation"])
        self.assertEqual(events[1]["content"], "管道检索回答")
        self.assertEqual(events[2]["message"], KB_DEGRADATION_NOTICE)

    def test_hyde_heartbeat_emitted_first(self):
        """use_hyde=True：首个事件为改写 heartbeat，随后检索 heartbeat。"""
        events, _, _, hyde_sync = self._collect(retrieve_docs=[_normal_doc()], use_hyde=True)

        hyde_sync.assert_called_once()
        self.assertEqual(events[0]["message"], "正在改写查询...")
        self.assertEqual(events[1]["message"], "正在检索文档...")


class AstreamStrictRagTests(unittest.IsolatedAsyncioTestCase):
    """astream_strict_rag：异步生成器事件序列与 async 降级路径。"""

    async def _collect(self, *, retrieve_docs=None, retrieve_exc=None, fallback_docs=None, kb_docs=None, kb_tokens=0):
        pipeline_cls, instance = _make_pipeline(retrieve_docs=retrieve_docs, retrieve_exc=retrieve_exc)
        fake_llm = _FakeLLM()
        with (
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline", pipeline_cls),
            mock.patch(f"{_RAG_RETRIEVAL}.load_kb_documents", return_value=(list(kb_docs or []), kb_tokens)) as load_kb,
            mock.patch(
                f"{_RETRIEVAL_SERVICE}._keyword_search_fallback",
                return_value=list(fallback_docs or []),
            ) as fallback,
            mock.patch(f"{_RETRIEVAL_SERVICE}._record_degradation_metric") as record_metric,
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
            mock.patch(f"{_STRICT_RAG}._hyde_rewrite_query", mock.MagicMock(return_value="改写后的假设文档")),
        ):
            events = []
            async for event in astream_strict_rag(
                mock.MagicMock(), _QUERY, k=_K, use_hyde=False, collection_name=_COLLECTION
            ):
                events.append(event)
        return events, instance, fallback, record_metric, load_kb, fake_llm

    async def test_normal_event_sequence(self):
        """正常路径事件序列与 sync 版一致（use_hyde=False）。"""
        events, _, _, _, _, _ = await self._collect(retrieve_docs=[_normal_doc()])

        self.assertEqual(
            [e["type"] for e in events],
            ["heartbeat", "heartbeat", "chunk", "chunk", "sources"],
        )
        self.assertEqual(events[0]["message"], "正在检索文档...")
        self.assertEqual([e["content"] for e in events[2:4]], ["回答甲", "回答乙"])

    async def test_embedding_error_fallback_positional_args(self):
        """embedding 故障：async 版降级经 to_thread 位置传参 (query, collection, k)，无 kwargs。"""
        events, _, fallback, record_metric, _, _ = await self._collect(
            retrieve_exc=ConnectionError("Connection refused"), fallback_docs=[_degraded_doc()]
        )

        fallback.assert_called_once()
        self.assertEqual(fallback.call_args.args, (_QUERY, _COLLECTION, _K))
        self.assertEqual(fallback.call_args.kwargs, {})
        record_metric.assert_called_once()
        types = [e["type"] for e in events]
        self.assertEqual(types, ["heartbeat", "heartbeat", "chunk", "chunk", "degradation", "sources"])
        self.assertEqual(events[4]["message"], KEYWORD_DEGRADATION_NOTICE)

    async def test_empty_result_branch_positional_args(self):
        """空结果分支：load_kb_documents 位置传参 ([collection],)，事件序列 chunk + degradation。"""
        events, _, _, _, load_kb, _ = await self._collect(
            retrieve_docs=[], kb_docs=[_normal_doc()], kb_tokens=7
        )

        load_kb.assert_called_once()
        self.assertEqual(load_kb.call_args.args, ([_COLLECTION],))
        self.assertEqual([e["type"] for e in events], ["heartbeat", "chunk", "degradation"])
        self.assertEqual(events[1]["content"], "管道检索回答")
        self.assertEqual(events[2]["message"], KB_DEGRADATION_NOTICE)


class DeadSymbolContractTests(unittest.TestCase):
    """死符号契约锁定：收敛后模块与 services 包的导出面。"""

    def test_dead_symbols_removed_from_module(self):
        """strict_rag_chain 模块不再定义死符号。"""
        from Django_xm.apps.knowledge.services import strict_rag_chain

        for name in ("aquery_strict_rag", "create_strict_rag_chain", "STRICT_RAG_SYSTEM_PROMPT", "_get_chat_model"):
            self.assertFalse(hasattr(strict_rag_chain, name), f"死符号未删除: {name}")

    def test_services_package_surface(self):
        """services 包仅 re-export 三个在役入口，死符号与 STRICT_RAG_QA_PROMPT 均不再导出。"""
        from Django_xm.apps.knowledge import services

        for name in ("query_strict_rag", "stream_strict_rag", "astream_strict_rag"):
            self.assertTrue(hasattr(services, name), f"在役入口缺失: {name}")
        for name in (
            "aquery_strict_rag",
            "create_strict_rag_chain",
            "STRICT_RAG_SYSTEM_PROMPT",
            "STRICT_RAG_QA_PROMPT",
        ):
            self.assertFalse(hasattr(services, name), f"死 re-export 未删除: {name}")


if __name__ == "__main__":
    unittest.main()
