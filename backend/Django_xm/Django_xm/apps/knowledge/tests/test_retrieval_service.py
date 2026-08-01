"""retrieval_service.py 单元测试。

覆盖范围：
- _is_embedding_error：异常分类（ConnectionError/timeout/embedding 等 10 种模式）
- QueryIntentClassifier._parse_intent：LLM 响应解析（JSON 提取 / 非法回退）
- QueryIntentClassifier._check_cache / _set_cache：意图分类缓存
- SyncSafeRetrieverTool._clean_docs：文档清理（空内容/JSON/短问句过滤）
- SyncSafeRetrieverTool._format_docs：文档格式化（数量限制/长度截断/来源标注）
- get_retriever_config：检索器配置（3 种类型 + 默认回退）
- MapReduceDocCombiner._should_use_map_reduce：阈值判断
- _RRFEnsembleRetriever：RRF 融合算法（mock 检索器）

设计原则：
- 不依赖真实 LLM / 向量库 / Redis
- 所有 LLM/Retriever 调用通过 mock 注入
- 使用 unittest.TestCase，DB 由 conftest 管理
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch

from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from Django_xm.apps.knowledge.services.retrieval_service import (
    MAX_DOC_CONTENT_LENGTH,
    MAX_DOCS_IN_RESULT,
    DegradableRetriever,
    MapReduceDocCombiner,
    QueryIntentClassifier,
    SyncSafeRetrieverTool,
    _is_embedding_error,
    create_retriever,
    get_retriever_config,
    wrap_with_degradation,
)


class _FakeBaseRetriever(BaseRetriever):
    """可编程的 BaseRetriever 子类，用于 DegradableRetriever/wrap_with_degradation 测试。

    通过类属性 ``_invoke_return`` / ``_invoke_side_effect`` 控制行为，
    避免在 Pydantic 模型字段中保存 MagicMock 触发校验报错。
    """

    _invoke_return: list = None  # type: ignore[assignment]
    _invoke_side_effect: BaseException = None  # type: ignore[assignment]

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        if self._invoke_side_effect is not None:
            raise self._invoke_side_effect
        return list(self._invoke_return or [])

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        if self._invoke_side_effect is not None:
            raise self._invoke_side_effect
        return list(self._invoke_return or [])


# ============================================================================
# _is_embedding_error 单测
# ============================================================================


class IsEmbeddingErrorTests(unittest.TestCase):
    """_is_embedding_error 异常分类。"""

    def test_connection_error(self):
        err = ConnectionError("connection refused")
        self.assertTrue(_is_embedding_error(err))

    def test_connection_refused_error(self):
        err = ConnectionRefusedError("refused")
        self.assertTrue(_is_embedding_error(err))

    def test_timeout_in_message(self):
        err = RuntimeError("request timeout")
        self.assertTrue(_is_embedding_error(err))

    def test_embedding_in_message(self):
        err = ValueError("embedding service error")
        self.assertTrue(_is_embedding_error(err))

    def test_ollama_in_message(self):
        err = RuntimeError("ollama not running")
        self.assertTrue(_is_embedding_error(err))

    def test_403_in_message(self):
        err = Exception("HTTP 403 Forbidden")
        self.assertTrue(_is_embedding_error(err))

    def test_503_in_message(self):
        err = Exception("HTTP 503 Service Unavailable")
        self.assertTrue(_is_embedding_error(err))

    def test_insufficient_balance(self):
        err = RuntimeError("insufficient_balance")
        self.assertTrue(_is_embedding_error(err))

    def test_max_retries_error(self):
        err = Exception("MaxRetriesError reached")
        self.assertTrue(_is_embedding_error(err))

    def test_unrelated_error_returns_false(self):
        err = ValueError("invalid input data")
        self.assertFalse(_is_embedding_error(err))

    def test_keyerror_returns_false(self):
        err = KeyError("missing_field")
        self.assertFalse(_is_embedding_error(err))

    def test_type_error_returns_false(self):
        err = TypeError("unsupported type")
        self.assertFalse(_is_embedding_error(err))

    def test_case_insensitive(self):
        err = RuntimeError("TIMEOUT occurred")
        self.assertTrue(_is_embedding_error(err))


# ============================================================================
# QueryIntentClassifier._parse_intent 单测
# ============================================================================


class ParseIntentTests(unittest.TestCase):
    """_parse_intent LLM 响应解析。"""

    def setUp(self):
        self.classifier = QueryIntentClassifier(llm=None)

    def test_valid_precise_json(self):
        response = '{"intent": "precise"}'
        self.assertEqual(self.classifier._parse_intent(response), "precise")

    def test_valid_comprehensive_json(self):
        response = '{"intent": "comprehensive"}'
        self.assertEqual(self.classifier._parse_intent(response), "comprehensive")

    def test_json_embedded_in_text(self):
        response = 'The intent is {"intent": "comprehensive"} based on analysis'
        self.assertEqual(self.classifier._parse_intent(response), "comprehensive")

    def test_missing_intent_field_defaults_precise(self):
        response = '{"other": "value"}'
        self.assertEqual(self.classifier._parse_intent(response), "precise")

    def test_invalid_intent_value_defaults_precise(self):
        response = '{"intent": "unknown_mode"}'
        self.assertEqual(self.classifier._parse_intent(response), "precise")

    def test_no_json_returns_precise(self):
        response = "no json here just text"
        self.assertEqual(self.classifier._parse_intent(response), "precise")

    def test_empty_string_returns_precise(self):
        self.assertEqual(self.classifier._parse_intent(""), "precise")

    def test_malformed_json_returns_precise(self):
        response = '{"intent": broken}'
        self.assertEqual(self.classifier._parse_intent(response), "precise")

    def test_non_string_input_converted(self):
        # 非字符串输入应被 str() 转换后解析
        self.assertEqual(self.classifier._parse_intent(12345), "precise")

    def test_none_input_returns_precise(self):
        self.assertEqual(self.classifier._parse_intent(None), "precise")


# ============================================================================
# QueryIntentClassifier 缓存单测
# ============================================================================


class IntentClassifierCacheTests(unittest.TestCase):
    """_check_cache / _set_cache 缓存操作。"""

    def setUp(self):
        self.classifier = QueryIntentClassifier(llm=None)

    def test_cache_miss_returns_none(self):
        self.assertIsNone(self.classifier._check_cache("new query"))

    def test_cache_hit_returns_intent(self):
        self.classifier._set_cache("query1", "comprehensive")
        self.assertEqual(self.classifier._check_cache("query1"), "comprehensive")

    def test_cache_overwrite(self):
        self.classifier._set_cache("query1", "precise")
        self.classifier._set_cache("query1", "comprehensive")
        self.assertEqual(self.classifier._check_cache("query1"), "comprehensive")

    def test_cache_expiry(self):
        # 手动设置过期时间戳
        self.classifier._set_cache("query1", "precise")
        # 模拟过期
        self.classifier._cache["query1"]["ts"] = time.time() - self.classifier._cache_ttl - 1
        self.assertIsNone(self.classifier._check_cache("query1"))

    def test_different_queries_separate(self):
        self.classifier._set_cache("query1", "precise")
        self.classifier._set_cache("query2", "comprehensive")
        self.assertEqual(self.classifier._check_cache("query1"), "precise")
        self.assertEqual(self.classifier._check_cache("query2"), "comprehensive")


# ============================================================================
# SyncSafeRetrieverTool._clean_docs 单测
# ============================================================================


class CleanDocsTests(unittest.TestCase):
    """_clean_docs 文档清理。"""

    def test_empty_list(self):
        self.assertEqual(SyncSafeRetrieverTool._clean_docs([]), [])

    def test_filters_empty_content(self):
        docs = [
            Document(page_content="", metadata={}),
            Document(page_content="   ", metadata={}),
            Document(page_content="valid", metadata={}),
        ]
        cleaned = SyncSafeRetrieverTool._clean_docs(docs)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].page_content, "valid")

    def test_filters_json_intent_data(self):
        # 意图分类 JSON（< 300 字符）应被过滤
        docs = [
            Document(page_content='{"intent": "precise"}', metadata={}),
            Document(page_content="valid content here", metadata={}),
        ]
        cleaned = SyncSafeRetrieverTool._clean_docs(docs)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].page_content, "valid content here")

    def test_keeps_long_json(self):
        # > 300 字符的 JSON 不被过滤（可能是真实文档内容）
        long_json = '{"data": "' + "x" * 400 + '"}'
        docs = [Document(page_content=long_json, metadata={})]
        cleaned = SyncSafeRetrieverTool._clean_docs(docs)
        self.assertEqual(len(cleaned), 1)

    def test_filters_short_questions(self):
        # < 150 字符且以问号结尾的 MultiQuery 替代查询应被过滤
        docs = [
            Document(page_content="如何配置数据库？", metadata={}),
            Document(page_content="how to configure database?", metadata={}),
            Document(page_content="valid document content", metadata={}),
        ]
        cleaned = SyncSafeRetrieverTool._clean_docs(docs)
        self.assertEqual(len(cleaned), 1)

    def test_keeps_long_questions(self):
        # >= 150 字符的问句可能是真实文档
        long_question = "这是一个非常长的问题" + "x" * 150 + "？"
        docs = [Document(page_content=long_question, metadata={})]
        cleaned = SyncSafeRetrieverTool._clean_docs(docs)
        self.assertEqual(len(cleaned), 1)

    def test_preserves_metadata(self):
        docs = [Document(page_content="content", metadata={"source": "file.pdf", "page": 1})]
        cleaned = SyncSafeRetrieverTool._clean_docs(docs)
        self.assertEqual(cleaned[0].metadata["source"], "file.pdf")
        self.assertEqual(cleaned[0].metadata["page"], 1)


# ============================================================================
# SyncSafeRetrieverTool._format_docs 单测
# ============================================================================


class FormatDocsTests(unittest.TestCase):
    """_format_docs 文档格式化。"""

    def test_empty_docs(self):
        self.assertEqual(SyncSafeRetrieverTool._format_docs([]), "未找到相关文档。")

    def test_single_doc(self):
        docs = [Document(page_content="内容", metadata={"source": "file.pdf"})]
        result = SyncSafeRetrieverTool._format_docs(docs)
        self.assertIn("[1]", result)
        self.assertIn("file.pdf", result)
        self.assertIn("内容", result)

    def test_multiple_docs_numbered(self):
        docs = [Document(page_content=f"文档{i}", metadata={"source": f"file{i}.pdf"}) for i in range(3)]
        result = SyncSafeRetrieverTool._format_docs(docs)
        self.assertIn("[1]", result)
        self.assertIn("[2]", result)
        self.assertIn("[3]", result)

    def test_limits_to_max_docs(self):
        docs = [Document(page_content=f"文档{i}", metadata={"source": "src"}) for i in range(MAX_DOCS_IN_RESULT + 5)]
        result = SyncSafeRetrieverTool._format_docs(docs)
        # 应包含截断提示
        self.assertIn("[注:", result)
        self.assertIn(f"共检索到 {MAX_DOCS_IN_RESULT + 5} 个文档", result)

    def test_truncates_long_content(self):
        long_content = "A" * (MAX_DOC_CONTENT_LENGTH + 100)
        docs = [Document(page_content=long_content, metadata={"source": "src"})]
        result = SyncSafeRetrieverTool._format_docs(docs)
        self.assertIn("...[内容已截断]", result)

    def test_keeps_short_content(self):
        short_content = "短内容"
        docs = [Document(page_content=short_content, metadata={"source": "src"})]
        result = SyncSafeRetrieverTool._format_docs(docs)
        self.assertNotIn("[内容已截断]", result)

    def test_unknown_source(self):
        docs = [Document(page_content="内容", metadata={})]
        result = SyncSafeRetrieverTool._format_docs(docs)
        self.assertIn("未知来源", result)

    def test_docs_separated_by_double_newline(self):
        docs = [
            Document(page_content="文档1", metadata={"source": "a"}),
            Document(page_content="文档2", metadata={"source": "b"}),
        ]
        result = SyncSafeRetrieverTool._format_docs(docs)
        self.assertIn("\n\n", result)


# ============================================================================
# get_retriever_config 单测
# ============================================================================


class GetRetrieverConfigTests(unittest.TestCase):
    """get_retriever_config 检索器配置。"""

    def test_similarity_config(self):
        config = get_retriever_config("similarity")
        self.assertEqual(config["search_type"], "similarity")
        self.assertEqual(config["k"], 4)
        self.assertNotIn("description", config)

    def test_mmr_config(self):
        config = get_retriever_config("mmr")
        self.assertEqual(config["search_type"], "mmr")
        self.assertEqual(config["k"], 4)
        self.assertEqual(config["fetch_k"], 20)

    def test_threshold_config(self):
        config = get_retriever_config("threshold")
        self.assertEqual(config["search_type"], "similarity_score_threshold")
        self.assertEqual(config["score_threshold"], 0.7)
        self.assertEqual(config["k"], 10)

    def test_unknown_type_falls_back_to_similarity(self):
        config = get_retriever_config("unknown_type")
        self.assertEqual(config["search_type"], "similarity")
        self.assertEqual(config["k"], 4)

    def test_empty_string_falls_back(self):
        config = get_retriever_config("")
        self.assertEqual(config["search_type"], "similarity")

    def test_description_removed_from_output(self):
        config = get_retriever_config("mmr")
        self.assertNotIn("description", config)


# ============================================================================
# MapReduceDocCombiner._should_use_map_reduce 单测
# ============================================================================


class ShouldUseMapReduceTests(unittest.TestCase):
    """_should_use_map_reduce 阈值判断。"""

    def test_below_threshold_returns_false(self):
        # batch_size 默认值由 app_cfg 决定，mock 为 10 → 阈值 15
        combiner = MapReduceDocCombiner(llm=None, batch_size=10)
        self.assertFalse(combiner._should_use_map_reduce(5))
        self.assertFalse(combiner._should_use_map_reduce(15))

    def test_above_threshold_returns_true(self):
        combiner = MapReduceDocCombiner(llm=None, batch_size=10)
        self.assertTrue(combiner._should_use_map_reduce(16))
        self.assertTrue(combiner._should_use_map_reduce(100))

    def test_custom_batch_size(self):
        combiner = MapReduceDocCombiner(llm=None, batch_size=4)
        # 阈值 = 4 * 1.5 = 6
        self.assertFalse(combiner._should_use_map_reduce(6))
        self.assertTrue(combiner._should_use_map_reduce(7))

    def test_zero_docs_returns_false(self):
        combiner = MapReduceDocCombiner(llm=None, batch_size=10)
        self.assertFalse(combiner._should_use_map_reduce(0))


# ============================================================================
# MapReduceDocCombiner._get_batch_size 单测
# ============================================================================


class GetBatchSizeTests(unittest.TestCase):
    """_get_batch_size 批次大小获取。"""

    def test_explicit_batch_size(self):
        combiner = MapReduceDocCombiner(llm=None, batch_size=8)
        self.assertEqual(combiner._get_batch_size(), 8)

    def test_none_batch_size_uses_config(self):
        combiner = MapReduceDocCombiner(llm=None, batch_size=None)
        # 不 mock 时读取 app_cfg，验证不报错且返回正整数
        size = combiner._get_batch_size()
        self.assertIsInstance(size, int)
        self.assertGreater(size, 0)


# ============================================================================
# MapReduceDocCombiner.combine_sync 单测（mock LLM）
# ============================================================================


class CombineSyncTests(unittest.TestCase):
    """combine_sync 端到端（mock LLM，doc_count ≤ 阈值走直接合并）。"""

    def test_empty_docs(self):
        combiner = MapReduceDocCombiner(llm=None, batch_size=10)
        result = combiner.combine_sync([], "query")
        # 空文档应返回某种提示文本，不报错
        self.assertIsInstance(result, str)

    def test_few_docs_no_map_reduce(self):
        # doc_count ≤ batch_size * 1.5，不触发 map_reduce
        mock_llm = MagicMock()
        docs = [Document(page_content=f"文档{i}", metadata={"source": "src"}) for i in range(3)]
        combiner = MapReduceDocCombiner(llm=mock_llm, batch_size=10)
        result = combiner.combine_sync(docs, "查询", llm=mock_llm)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


# ============================================================================
# _RRFEnsembleRetriever 单测（mock 子检索器）
# ============================================================================


class RRFEnsembleRetrieverTests(unittest.TestCase):
    """_RRFEnsembleRetriever RRF 融合算法。

    _RRFEnsembleRetriever 继承 BaseRetriever（Pydantic v2 模型），
    __init__ 会校验 retrievers 为 BaseRetriever 实例。
    用 model_construct() 绕过校验，注入 fake 检索器（duck-type invoke）。
    """

    def _build_ensemble(self, fake_retrievers, weights, k=4, c=60):
        from Django_xm.apps.knowledge.services.retrieval_service import (
            _RRFEnsembleRetriever,
        )

        instance = _RRFEnsembleRetriever.model_construct(
            retrievers=fake_retrievers,
            weights=weights,
            k=k,
            c=c,
        )
        return instance

    def test_merges_and_ranks_by_rrf(self):
        # 两个 fake 检索器，返回部分重叠的文档
        r1_docs = [
            Document(page_content="doc_A", metadata={"source": "s1"}),
            Document(page_content="doc_B", metadata={"source": "s1"}),
        ]
        r2_docs = [
            Document(page_content="doc_B", metadata={"source": "s2"}),
            Document(page_content="doc_C", metadata={"source": "s2"}),
        ]

        class _FakeR1:
            def invoke(self, query):
                return r1_docs

        class _FakeR2:
            def invoke(self, query):
                return r2_docs

        ensemble = self._build_ensemble([_FakeR1(), _FakeR2()], [0.5, 0.5])
        results = ensemble.invoke("query")
        contents = [d.page_content for d in results]
        self.assertEqual(len(contents), len(set(contents)))  # 无重复
        self.assertIn("doc_A", contents)
        self.assertIn("doc_B", contents)
        self.assertIn("doc_C", contents)
        # doc_B 在两个检索器中都出现，RRF 分数应最高
        self.assertEqual(contents[0], "doc_B")

    def test_empty_retrievers_results(self):
        class _FakeEmpty:
            def invoke(self, query):
                return []

        ensemble = self._build_ensemble([_FakeEmpty()], [1.0])
        results = ensemble.invoke("query")
        self.assertEqual(results, [])

    def test_respects_k_limit(self):
        docs = [Document(page_content=f"doc_{i}", metadata={"source": "s"}) for i in range(10)]

        class _FakeR:
            def invoke(self, query):
                return docs

        ensemble = self._build_ensemble([_FakeR()], [1.0], k=3)
        results = ensemble.invoke("query")
        self.assertLessEqual(len(results), 3)


# ============================================================================
# DegradableRetriever 单测
# ============================================================================


class DegradableRetrieverTests(unittest.TestCase):
    """DegradableRetriever 降级逻辑。

    正常路径委托 base_retriever.invoke；embedding 错误降级到
    _keyword_search_fallback；非 embedding 错误原样抛出。
    使用 _FakeBaseRetriever（真实 BaseRetriever 子类）作为 base_retriever，
    避免触发 Pydantic 对 arbitrary 类型的校验报错。
    """

    def _make_retriever(self, base_retriever, collection_name="test_coll", fallback_k=4):
        return DegradableRetriever(
            base_retriever=base_retriever,
            collection_name=collection_name,
            fallback_k=fallback_k,
        )

    def _make_base(self, return_docs=None, side_effect=None):
        base = _FakeBaseRetriever()
        base._invoke_return = return_docs or []
        base._invoke_side_effect = side_effect
        return base

    def test_normal_path_delegates_to_base_retriever(self):
        expected = [Document(page_content="命中", metadata={})]
        base = self._make_base(return_docs=expected)
        retriever = self._make_retriever(base)
        results = retriever.invoke("query")
        self.assertEqual(results, expected)

    def test_embedding_error_triggers_keyword_fallback(self):
        base = self._make_base(side_effect=ConnectionError("connection refused"))
        fallback_docs = [Document(page_content="降级结果", metadata={"degraded": True})]
        retriever = self._make_retriever(base, collection_name="coll_x", fallback_k=3)
        with patch(
            "Django_xm.apps.knowledge.services.retrieval_service._keyword_search_fallback",
            return_value=fallback_docs,
        ) as mock_fb:
            results = retriever.invoke("query")
        # _keyword_search_fallback 以关键字 k= 传参
        mock_fb.assert_called_once_with("query", "coll_x", k=3)
        self.assertEqual(results, fallback_docs)

    def test_non_embedding_error_reraises(self):
        base = self._make_base(side_effect=KeyError("missing_field"))
        retriever = self._make_retriever(base, collection_name="coll_x")
        with (
            patch("Django_xm.apps.knowledge.services.retrieval_service._keyword_search_fallback") as mock_fb,
            self.assertRaises(KeyError),
        ):
            retriever.invoke("query")
        mock_fb.assert_not_called()

    def test_empty_collection_name_skips_metric_recording(self):
        base = self._make_base(side_effect=ConnectionError("refused"))
        retriever = self._make_retriever(base, collection_name="")
        with (
            patch(
                "Django_xm.apps.knowledge.services.retrieval_service._keyword_search_fallback",
                return_value=[],
            ),
            patch("Django_xm.apps.knowledge.services.retrieval_service._record_degradation_metric") as mock_metric,
        ):
            results = retriever.invoke("query")
        self.assertEqual(results, [])
        mock_metric.assert_not_called()

    def test_collection_name_triggers_metric_recording(self):
        err = ConnectionError("refused")
        base = self._make_base(side_effect=err)
        retriever = self._make_retriever(base, collection_name="coll_x")
        with (
            patch(
                "Django_xm.apps.knowledge.services.retrieval_service._keyword_search_fallback",
                return_value=[],
            ),
            patch("Django_xm.apps.knowledge.services.retrieval_service._record_degradation_metric") as mock_metric,
        ):
            retriever.invoke("query")
        mock_metric.assert_called_once()
        # 第二参数为触发的异常对象
        self.assertEqual(mock_metric.call_args.args[0], "coll_x")


class DegradableRetrieverAsyncTests(unittest.TestCase):
    """DegradableRetriever 异步降级路径。"""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_async_normal_path(self):
        expected = [Document(page_content="async 命中", metadata={})]
        base = _FakeBaseRetriever()
        base._invoke_return = expected
        retriever = DegradableRetriever(base_retriever=base, collection_name="c", fallback_k=4)

        async def _run():
            return await retriever.ainvoke("query")

        results = self.loop.run_until_complete(_run())
        self.assertEqual(results, expected)

    def test_async_embedding_error_triggers_fallback(self):
        base = _FakeBaseRetriever()
        base._invoke_side_effect = ConnectionError("timeout")
        fallback_docs = [Document(page_content="async 降级", metadata={})]

        async def _run():
            retriever = DegradableRetriever(base_retriever=base, collection_name="c_async", fallback_k=2)
            return await retriever.ainvoke("query")

        with patch(
            "Django_xm.apps.knowledge.services.retrieval_service._keyword_search_fallback",
            return_value=fallback_docs,
        ) as mock_fb:
            results = self.loop.run_until_complete(_run())
        # 异步路径通过 asyncio.to_thread 调用，fallback_k 为位置参数
        mock_fb.assert_called_once_with("query", "c_async", 2)
        self.assertEqual(results, fallback_docs)


# ============================================================================
# wrap_with_degradation 单测
# ============================================================================


class WrapWithDegradationTests(unittest.TestCase):
    """wrap_with_degradation 包装与幂等。"""

    def test_wraps_plain_retriever(self):
        base = _FakeBaseRetriever()
        wrapped = wrap_with_degradation(base, collection_name="coll", fallback_k=5)
        self.assertIsInstance(wrapped, DegradableRetriever)
        self.assertIs(wrapped.base_retriever, base)
        self.assertEqual(wrapped.collection_name, "coll")
        self.assertEqual(wrapped.fallback_k, 5)

    def test_idempotent_already_degradable(self):
        base = _FakeBaseRetriever()
        wrapped_once = wrap_with_degradation(base, collection_name="coll")
        wrapped_twice = wrap_with_degradation(wrapped_once, collection_name="coll")
        self.assertIs(wrapped_once, wrapped_twice)

    def test_default_collection_name_empty(self):
        base = _FakeBaseRetriever()
        wrapped = wrap_with_degradation(base)
        self.assertEqual(wrapped.collection_name, "")
        self.assertEqual(wrapped.fallback_k, 4)


# ============================================================================
# create_retriever 单测
# ============================================================================


class CreateRetrieverTests(unittest.TestCase):
    """create_retriever 检索器配置。

    vector_store.as_retriever 通过 mock 注入，验证 search_type/search_kwargs
    映射与 use_reranker 分支。
    """

    def _make_vector_store(self):
        vs = MagicMock()
        retriever = MagicMock(name="retriever")
        vs.as_retriever.return_value = retriever
        return vs, retriever

    def test_similarity_type(self):
        vs, retriever = self._make_vector_store()
        result = create_retriever(vs, search_type="similarity", k=5)
        vs.as_retriever.assert_called_once()
        _, kwargs = vs.as_retriever.call_args
        self.assertEqual(kwargs["search_type"], "similarity")
        self.assertEqual(kwargs["search_kwargs"]["k"], 5)
        self.assertIs(result, retriever)

    def test_mmr_type_includes_fetch_k(self):
        vs, _ = self._make_vector_store()
        create_retriever(vs, search_type="mmr", k=4, fetch_k=20)
        _, kwargs = vs.as_retriever.call_args
        self.assertEqual(kwargs["search_type"], "mmr")
        self.assertEqual(kwargs["search_kwargs"]["k"], 4)
        self.assertEqual(kwargs["search_kwargs"]["fetch_k"], 20)

    def test_threshold_type_includes_score_threshold(self):
        vs, _ = self._make_vector_store()
        create_retriever(vs, search_type="similarity_score_threshold", k=10, score_threshold=0.8)
        _, kwargs = vs.as_retriever.call_args
        self.assertEqual(kwargs["search_type"], "similarity_score_threshold")
        self.assertEqual(kwargs["search_kwargs"]["score_threshold"], 0.8)
        self.assertEqual(kwargs["search_kwargs"]["k"], 10)

    def test_extra_kwargs_merged_into_search_kwargs(self):
        vs, _ = self._make_vector_store()
        create_retriever(vs, search_type="similarity", k=4, custom_filter={"lang": "zh"})
        _, kwargs = vs.as_retriever.call_args
        self.assertEqual(kwargs["search_kwargs"]["custom_filter"], {"lang": "zh"})

    def test_use_reranker_false_skips_reranker(self):
        vs, retriever = self._make_vector_store()
        result = create_retriever(vs, search_type="similarity", k=4, use_reranker=False)
        self.assertIs(result, retriever)

    def test_use_reranker_true_wraps_when_reranker_available(self):
        vs, retriever = self._make_vector_store()
        reranker = MagicMock(name="reranker")
        reranking_retriever = MagicMock(name="reranking_retriever")
        with (
            patch(
                "Django_xm.apps.knowledge.services.retrieval_service.create_reranker",
                return_value=reranker,
            ),
            patch(
                "Django_xm.apps.knowledge.services.retrieval_service.create_reranking_retriever",
                return_value=reranking_retriever,
            ) as mock_rr,
        ):
            result = create_retriever(vs, search_type="similarity", k=4, use_reranker=True)
        mock_rr.assert_called_once_with(retriever, reranker)
        self.assertIs(result, reranking_retriever)

    def test_use_reranker_true_skips_when_reranker_none(self):
        vs, retriever = self._make_vector_store()
        with patch(
            "Django_xm.apps.knowledge.services.retrieval_service.create_reranker",
            return_value=None,
        ):
            result = create_retriever(vs, search_type="similarity", k=4, use_reranker=True)
        self.assertIs(result, retriever)

    def test_vector_store_failure_reraises(self):
        vs = MagicMock()
        vs.as_retriever.side_effect = RuntimeError("vector store init failed")
        with self.assertRaises(RuntimeError):
            create_retriever(vs, search_type="similarity", k=4)


if __name__ == "__main__":
    unittest.main()
