"""C-3 + CT-06：strict_rag embedding 降级分支可达性与类型化错误契约。

修复前缺口（rag_audit_chain C-3 / rag_audit_contract CT-06）：
- C-3：strict_rag 三入口的"embedding 故障→PG 关键词降级"分支生产不可达——try 体
  调用的 ``UnifiedRagPipeline.retrieve_documents_from_retriever`` 自身吞一切异常
  返回 []，embedding 故障实际走 _handle_empty_result（小库整库全文注入，文案误导
  为 KB_DEGRADATION"未命中"）；既有测试以 ``side_effect`` 强制 mock 抛异常掩盖
  （假覆盖）；
- CT-06：``is_embedding_error`` 依赖 13 个字符串模式匹配，ai_engine 全败时原样
  重抛末位 provider 异常，模式未命中即整查询失败。

修复形态：
- C-3：``retrieve_documents_from_retriever``/``_initial_search_from_retriever``
  增 ``raise_on_error`` 参数（默认 False 保持学习工作流等既有调用方吞异常语义）；
  strict_rag_chain 两入口检索传 ``raise_on_error=True`` 使降级分支可达；
- CT-06：ai_engine 全败时包装为类型化 ``EmbeddingUnavailableError``
  （common.exceptions，消息保留原错误文本）；``is_embedding_error`` isinstance
  优先判定，字符串模式降级为兜底。

测试证明方式（mock 结构反转）：不再 mock UnifiedRagPipeline 类强制抛异常，而用
**真实 Pipeline** + retriever.invoke 抛真实异常，验证降级分支经真实异常传播路径
可达（修复前：真实 Pipeline 吞异常 → docs=[] → 走全文注入分支，测试必败）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_strict_rag_degradation_reachable
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline
from Django_xm.apps.knowledge.services.keyword_fallback import is_embedding_error
from Django_xm.apps.knowledge.services.strict_rag_chain import (
    KEYWORD_DEGRADATION_NOTICE,
    NO_RESULT_ANSWER,
    astream_strict_rag,
    query_strict_rag,
    stream_strict_rag,
)
from Django_xm.common.exceptions import EmbeddingUnavailableError
from Django_xm.common.testing import create_fake_llm

_STRICT_RAG = "Django_xm.apps.knowledge.services.strict_rag_chain"
_KEYWORD_FALLBACK = "Django_xm.apps.knowledge.services.keyword_fallback"
_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"

_QUERY = "什么是 LangChain"
_COLLECTION = "test_collection"
_K = 6


def _degraded_doc():
    return Document(page_content="关键词降级检索内容", metadata={"degraded": True, "source": "kb.md"})


def _failing_retriever(exc=None):
    """检索器桩：invoke 抛真实异常（embedding 故障形态），而非 mock Pipeline 抛。"""
    retriever = mock.MagicMock(name="retriever")
    retriever.invoke.side_effect = exc or ConnectionError("Connection refused")
    return retriever


class ReachabilityBehaviorTests(unittest.TestCase):
    """C-3 行为测试：真实 Pipeline + 真实异常传播 → 降级分支可达。"""

    def _run_query(self, retriever, fallback_docs):
        """query_strict_rag 全链（真实 UnifiedRagPipeline，仅屏蔽 PG SQL/LLM 依赖）。"""
        fake_llm = create_fake_llm()
        with (
            mock.patch(f"{_KEYWORD_FALLBACK}.keyword_search_fallback", return_value=list(fallback_docs)) as fallback,
            mock.patch(f"{_KEYWORD_FALLBACK}.record_degradation_metric") as record_metric,
            mock.patch(f"{_RAG_RETRIEVAL}.load_kb_documents", return_value=([], 0)),
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
        ):
            result = query_strict_rag(
                retriever, _QUERY, k=_K, use_hyde=False, collection_name=_COLLECTION
            )
        return result, fallback, record_metric

    def test_embedding_failure_reaches_keyword_fallback(self):
        """embedding 故障（真实 Pipeline 传播）→ PG 关键词降级 + KEYWORD 文案。

        修复前：Pipeline 吞异常 → docs=[] → _handle_empty_result（全文注入分支），
        notice 是 KB_DEGRADATION，本测试断言的 KEYWORD 文案与降级调用均不可能成立。
        """
        retriever = _failing_retriever()
        doc = _degraded_doc()
        result, fallback, record_metric = self._run_query(retriever, [doc])

        self.assertTrue(result["success"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degradation_notice"], KEYWORD_DEGRADATION_NOTICE)
        self.assertEqual(result["retrieved_docs"], [doc])
        # 降级检索收到原始 query（非 HyDE 改写）与 k
        fallback.assert_called_once_with(_QUERY, _COLLECTION, k=_K)
        record_metric.assert_called_once()

    def test_embedding_failure_empty_fallback_keeps_keyword_degradation(self):
        """降级检索无结果且知识库无文档：degraded 仍为 True。"""
        retriever = _failing_retriever()
        result, fallback, _ = self._run_query(retriever, [])

        fallback.assert_called_once()
        self.assertEqual(result["answer"], NO_RESULT_ANSWER)
        self.assertTrue(result["degraded"])

    def test_non_embedding_error_still_propagates(self):
        """非 embedding 异常原样上抛（不误降级）。"""
        retriever = _failing_retriever(ValueError("业务参数错误"))
        with self.assertRaises(ValueError):
            self._run_query(retriever, [])

    def test_stream_entry_reaches_keyword_fallback(self):
        """流式入口同修复：degradation 事件携带 KEYWORD 文案。"""
        retriever = _failing_retriever()
        fake_llm = create_fake_llm()
        with (
            mock.patch(f"{_KEYWORD_FALLBACK}.keyword_search_fallback", return_value=[_degraded_doc()]),
            mock.patch(f"{_KEYWORD_FALLBACK}.record_degradation_metric"),
            mock.patch(f"{_RAG_RETRIEVAL}.load_kb_documents", return_value=([], 0)),
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
        ):
            events = list(
                stream_strict_rag(retriever, _QUERY, k=_K, use_hyde=False, collection_name=_COLLECTION)
            )

        types = [e["type"] for e in events]
        self.assertEqual(types, ["heartbeat", "degradation", "heartbeat", "chunk", "chunk", "sources"])
        self.assertEqual(events[1]["message"], KEYWORD_DEGRADATION_NOTICE)


class AstreamReachabilityTests(unittest.IsolatedAsyncioTestCase):
    """async 入口同修复（降级关键词检索仍经 to_thread 离开事件循环）。"""

    async def test_astream_embedding_failure_reaches_keyword_fallback(self):
        retriever = _failing_retriever()
        fake_llm = create_fake_llm()
        with (
            mock.patch(f"{_KEYWORD_FALLBACK}.keyword_search_fallback", return_value=[_degraded_doc()]) as fallback,
            mock.patch(f"{_KEYWORD_FALLBACK}.record_degradation_metric"),
            mock.patch(f"{_RAG_RETRIEVAL}.load_kb_documents", return_value=([], 0)),
            mock.patch(f"{_STRICT_RAG}._resolve_chat_model", return_value=fake_llm),
        ):
            events = []
            async for event in astream_strict_rag(
                retriever, _QUERY, k=_K, use_hyde=False, collection_name=_COLLECTION
            ):
                events.append(event)

        types = [e["type"] for e in events]
        self.assertEqual(
            types, ["heartbeat", "degradation", "heartbeat", "chunk", "chunk", "sources"]
        )
        self.assertEqual(events[1]["message"], KEYWORD_DEGRADATION_NOTICE)
        # 降级检索经 to_thread 位置传参 (query, collection, k)
        self.assertEqual(fallback.call_args.args, (_QUERY, _COLLECTION, _K))


class RaiseOnErrorContractTests(unittest.TestCase):
    """raise_on_error 参数契约：默认 False 保持吞异常语义（学习工作流等调用方）。"""

    def test_default_false_swallows_exceptions(self):
        """默认吞异常返回 []（retrieve_documents_from_retriever 既有契约）。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = _failing_retriever()

        docs = pipeline.retrieve_documents_from_retriever(retriever=retriever, query="q", vector_store=None)

        self.assertEqual(docs, [])

    def test_raise_true_propagates(self):
        """raise_on_error=True：异常穿透（含 _initial_search_from_retriever 内层）。"""
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        retriever = _failing_retriever()

        with self.assertRaises(ConnectionError):
            pipeline.retrieve_documents_from_retriever(
                retriever=retriever, query="q", vector_store=None, raise_on_error=True
            )

    def test_strict_chain_calls_with_raise_on_error(self):
        """strict_rag_chain 两侧入口必须以 raise_on_error=True 调 Pipeline（接线锁定）。"""
        from Django_xm.apps.knowledge.services import strict_rag_chain as src

        source = open(src.__file__, encoding="utf-8").read()
        # 匹配实际调用形态（docstring 中的 raise_on_error=True 提及不计）
        self.assertEqual(
            source.count("vector_store=None, raise_on_error=True"),
            2,
            "sync/async 两侧检索均须传 raise_on_error=True",
        )


class TypedErrorContractTests(unittest.TestCase):
    """CT-06 类型化契约：EmbeddingUnavailableError isinstance 优先 + 字符串兜底保持。"""

    def test_typed_error_detected_without_pattern_match(self):
        """类型化异常恒命中降级判定，即使消息不含任何既有模式词。"""
        exc = EmbeddingUnavailableError("totally unknown provider failure text")
        self.assertTrue(is_embedding_error(exc))

    def test_wrapped_original_message_preserved(self):
        """包装异常保留原始错误文本（供日志与既有子串消费方使用）。"""
        original = RuntimeError("insufficient_balance for key")
        wrapped = EmbeddingUnavailableError(f"所有 Embedding 提供商均失败: {original}")
        self.assertIn("insufficient_balance", str(wrapped))

    def test_string_patterns_still_work_as_fallback(self):
        """非包装异常的字符串模式判定保持（向后兼容兜底）。"""
        self.assertTrue(is_embedding_error(ConnectionError("Connection refused")))
        self.assertFalse(is_embedding_error(ValueError("业务参数错误")))


class EmbeddingFactoryTypedRaisingTests(unittest.TestCase):
    """ai_engine 全败分支：包装为 EmbeddingUnavailableError 后原样重抛。"""

    def test_all_providers_fail_raises_typed_error(self):
        from Django_xm.apps.ai_engine.services.embedding_factory import FallbackEmbedding

        class _Fail:
            def embed_query(self, text):
                raise RuntimeError("quota exhausted")

        fb = FallbackEmbedding([_Fail()], ["Only"], provider_ids=["p1"])
        with self.assertRaises(EmbeddingUnavailableError) as ctx:
            fb.embed_query("x")

        self.assertIn("quota exhausted", str(ctx.exception), "原始错误文本必须保留")
        self.assertIsNotNone(ctx.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
