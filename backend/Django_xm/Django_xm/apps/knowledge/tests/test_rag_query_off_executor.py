"""RP-01 + C-8 + CT-05：RAG 查询面异步化与流式/非流式行为对齐测试。

修复前缺口（rag_audit_perf RP-01 / rag_audit_chain C-8 / rag_audit_contract CT-05）：
- RAGQueryView.post 与 rag_query_stream 在 daphne ASGI 下全同步执行于
  thread_sensitive 主执行器——单条慢 RAG 请求（LLM 生成几十秒）串行化全站同步视图；
- 非流式缓存命中路径丢弃 degraded/degradation_notice（缓存响应形状≠新鲜响应形状）；
- 流式/非流式缓存策略与空索引语义两链不一致。

修复形态（对齐 learning_stream.pump_sync_events 线程泵先例 + DRF 3.17 dispatch
同步管道约束）：
- AsyncAPIView 基类重写 dispatch（DRF 管道轻活走主执行器，handler 为 async def）；
- RAGQueryView/SearchAllView handler async 化，重活经
  sync_to_async(thread_sensitive=False) 调度进独立线程池；
- rag_query_stream 保持 @api_view sync 前置段（鉴权/校验/空索引 JSON 错误契约），
  检索器构建与 stream_strict_rag 同步迭代移入线程泵，async 生成器出帧；
- 流式与非流式共用 QueryCacheService（命中合成事件序列，完成后写回）。

测试证明方式（参照 D1 时序死锁式模式 test_context_engineering_off_loop.py）：
1. 机制断言——handler 为 coroutine function、view_is_async 为 True；
2. 并行断言——两个并发请求的重活各阻塞在独立 Event 上，任一请求的重活开始
   时间早于另一请求的重活结束时间（thread_sensitive=True 主执行器串行下必败），
   且重活执行线程互不相同；
3. 契约断言——缓存命中 degraded 透传、流式事件序列（含缓存写回）、空索引 400。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_query_off_executor
"""

import asyncio
import inspect
import json
import os
import threading
import time
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from Django_xm.apps.knowledge import views_rag as vr
from Django_xm.apps.knowledge.views_rag import RAGQueryView, SearchAllView

_VIEWS_RAG = "Django_xm.apps.knowledge.views_rag"

# P1 埋点：模块级隔离 Celery broker（测试环境不投递真任务）
_METRICS_PATCHERS: list = []


def setUpModule():
    patcher = mock.patch(f"{_VIEWS_RAG}.retrieval_metrics.record_retrieval")
    patcher.start()
    _METRICS_PATCHERS.append(patcher)


def tearDownModule():
    while _METRICS_PATCHERS:
        _METRICS_PATCHERS.pop().stop()

_QUERY = "什么是 LangChain"
_INDEX = "kb1"

_FAKE_USER = SimpleNamespace(is_authenticated=True, id=1, pk=1, username="tester", email="t@t.com")


def _query_request(path="/api/v1/knowledge/query/"):
    factory = APIRequestFactory()
    request = factory.post(path, {"query": _QUERY, "index_name": "kb1", "k": 4}, format="json")
    force_authenticate(request, user=_FAKE_USER)
    return request


def _fake_metadata(num_documents=3, dim=1536):
    return {"num_documents": num_documents, "embedding_dimension": dim}


def _fake_bundle():
    """P0：views 层检索器构建门面返回 (retriever, vector_store) 打包对象。"""
    return SimpleNamespace(retriever=object(), vector_store=object())


class _FakeIndexManager:
    """IndexManager 桩：index_exists/load_metadata 可控，其余方法计数禁触。"""

    def __init__(self, index_exists=True, metadata=None):
        self._index_exists = index_exists
        self._metadata = metadata if metadata is not None else _fake_metadata()
        self.load_index_calls = 0

    def index_exists(self, name, user_id=None, store_type=None):
        return self._index_exists

    def load_metadata(self, name, user_id=None, store_type=None):
        return self._metadata

    def load_index(self, name, embeddings):
        self.load_index_calls += 1
        return object()

    def get_store_type(self, name):
        return "pgvector"

    def get_score_semantics(self, name):
        # C-2arch：与 get_store_type="pgvector" 对应的语义声明（等价替换）
        return "distance"


class AsyncViewMechanismTests(SimpleTestCase):
    """机制断言：handler 与 dispatch 的 async 形态（修复前 post 为 sync，必败）。"""

    def test_query_view_handlers_are_async(self):
        self.assertTrue(
            inspect.iscoroutinefunction(RAGQueryView.post), "RAGQueryView.post 必须是 async def"
        )
        self.assertTrue(
            inspect.iscoroutinefunction(RAGQueryView.dispatch),
            "AsyncAPIView.dispatch 必须是 async def（DRF dispatch 同步管道会吞 coroutine）",
        )
        self.assertTrue(RAGQueryView.view_is_async)

    def test_search_all_view_handlers_are_async(self):
        self.assertTrue(inspect.iscoroutinefunction(SearchAllView.post))
        self.assertTrue(SearchAllView.view_is_async)


class QueryViewOffExecutorTests(SimpleTestCase):
    """RP-01 并行断言：重活经 thread_sensitive=False 离开主执行器，可并发不串行。"""

    def test_concurrent_heavy_work_does_not_serialize(self):
        """两个并发查询请求的重活并行执行（修复前：sync post 全部串行在主执行器）。"""
        timeline = {"work_started": {}, "work_finished": {}, "work_threads": {}}
        releases = {0: threading.Event(), 1: threading.Event()}
        lock = threading.Lock()
        counter = {"n": 0}

        def _blocking_query(query, user_id, index_name, k):
            with lock:
                slot = counter["n"]
                counter["n"] += 1
            timeline["work_started"][slot] = time.perf_counter()
            timeline["work_threads"][slot] = threading.current_thread()
            releases[slot].wait(timeout=5.0)
            timeline["work_finished"][slot] = time.perf_counter()
            return {"answer": f"answer-{slot}", "sources": [], "success": True}

        manager = _FakeIndexManager()

        async def scenario():
            view = RAGQueryView.as_view()

            async def release_both():
                # 等两个请求都进入重活等待 → 放行槽 0 → 放行槽 1
                for _ in range(200):
                    if len(timeline["work_started"]) == 2:
                        break
                    await asyncio.sleep(0.01)
                releases[0].set()
                await asyncio.sleep(0.05)
                releases[1].set()

            releaser = asyncio.create_task(release_both())
            with (
                mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
                mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
                mock.patch(f"{_VIEWS_RAG}.QueryCacheService.cache_query_result"),
                mock.patch(f"{_VIEWS_RAG}.build_user_retriever_bundle", return_value=_fake_bundle()),
                mock.patch(
                    f"{_VIEWS_RAG}.query_strict_rag", return_value={"answer": "a", "sources": [], "success": True}
                ),
                mock.patch(f"{_VIEWS_RAG}._run_strict_query_sync", side_effect=_blocking_query),
            ):
                responses = await asyncio.gather(view(_query_request()), view(_query_request()))
            await releaser
            return responses

        responses = asyncio.run(scenario())

        # 断言 1：两个重活都在对方结束前启动（并行，非串行）
        self.assertEqual(set(timeline["work_started"]), {0, 1})
        first, second = sorted(timeline["work_started"], key=lambda s: timeline["work_started"][s])
        self.assertLess(
            timeline["work_started"][first],
            timeline["work_finished"][second],
            "第二个请求的重活应在前一个重活结束前启动（thread_sensitive=False 并行）",
        )
        # 断言 2：重活执行线程互不相同（thread_sensitive=True 主执行器是单线程）
        self.assertIsNot(timeline["work_threads"][0], timeline["work_threads"][1])
        # 断言 3：两请求均 200，且答案恰好来自两个独立重活实例（result 不串）
        answers = {response.data["data"]["answer"] for response in responses}
        self.assertEqual(answers, {"answer-0", "answer-1"})
        for response in responses:
            self.assertEqual(response.status_code, 200)


class QueryViewContractTests(SimpleTestCase):
    """CT-05 缓存命中形状 + 空索引/不存在索引错误语义（重活线程内抛出须透传 JSON 错误）。"""

    def test_cache_hit_returns_degraded_fields(self):
        """CT-05：缓存命中响应补 degraded/degradation_notice（修复前被丢弃，形状≠新鲜响应）。"""
        cached = {
            "result": {
                "answer": "缓存答案",
                "sources": [{"content": "片段", "source": "f.md", "metadata": {}}],
                "success": True,
                "degraded": True,
                "degradation_notice": "向量检索服务暂时不可用",
            }
        }
        with mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=cached):
            response = asyncio.run(RAGQueryView.as_view()(_query_request()))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["degraded"])
        self.assertEqual(response.data["data"]["degradation_notice"], "向量检索服务暂时不可用")
        # C-4：缓存命中路径 sources 同样结构化透传
        self.assertEqual(response.data["data"]["sources"][0]["source"], "f.md")

    def test_cache_hit_without_degraded_has_no_flag(self):
        cached = {"result": {"answer": "普通答案", "sources": [], "success": True}}
        with mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=cached):
            response = asyncio.run(RAGQueryView.as_view()(_query_request()))

        self.assertNotIn("degraded", response.data["data"])

    def test_empty_index_returns_400(self):
        """空索引 400 ValidationError（重活在线程内抛出 → dispatch except → JSON 错误）。"""
        manager = _FakeIndexManager(metadata=_fake_metadata(num_documents=0))
        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
        ):
            response = asyncio.run(RAGQueryView.as_view()(_query_request()))

        self.assertEqual(response.status_code, 400)
        self.assertIn("索引中暂无文档", response.data["message"])

    def test_missing_index_returns_404(self):
        manager = _FakeIndexManager(index_exists=False)
        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
        ):
            response = asyncio.run(RAGQueryView.as_view()(_query_request()))

        self.assertEqual(response.status_code, 404)

    def test_non_embedding_error_propagates_as_500(self):
        """重活线程内非 DRF 异常（如 strict RAG 内部错误）透传为 500 JSON，不静默。"""
        manager = _FakeIndexManager()
        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
            mock.patch(f"{_VIEWS_RAG}._run_strict_query_sync", side_effect=RuntimeError("boom")),
        ):
            response = asyncio.run(RAGQueryView.as_view()(_query_request()))

        self.assertEqual(response.status_code, 500)


class FinalizeResponseCacheTests(SimpleTestCase):
    """Task 4/B3：非流式 _finalize_response 空结果/降级不写查询缓存。"""

    @staticmethod
    def _prepared():
        from types import SimpleNamespace as NS

        # model_seg/mode_seg 为 B5 新增：_prepare_request 产出的 prepared 均携带
        return NS(
            query="什么是 LangChain",
            user_index_name="user_1_kb",
            user_id=1,
            k=4,
            model_seg="m-test",
            mode_seg="strict",
            started_at=time.perf_counter(),
        )

    def test_degraded_result_not_cached(self):
        cache_write = mock.MagicMock()
        with mock.patch(f"{_VIEWS_RAG}.QueryCacheService.cache_query_result", cache_write):
            RAGQueryView._finalize_response(
                self._prepared(), {"answer": "答案", "sources": [], "success": True, "degraded": True}
            )
        cache_write.assert_not_called()

    def test_empty_answer_not_cached(self):
        cache_write = mock.MagicMock()
        with mock.patch(f"{_VIEWS_RAG}.QueryCacheService.cache_query_result", cache_write):
            RAGQueryView._finalize_response(self._prepared(), {"answer": "", "sources": [], "success": True})
        cache_write.assert_not_called()

    def test_normal_result_cached(self):
        cache_write = mock.MagicMock()
        with mock.patch(f"{_VIEWS_RAG}.QueryCacheService.cache_query_result", cache_write):
            RAGQueryView._finalize_response(
                self._prepared(), {"answer": "答案", "sources": [], "success": True}
            )
        cache_write.assert_called_once()


def _collect_sse_events(chunks):
    events = []
    for raw in chunks:
        text = raw.decode() if isinstance(raw, bytes) else raw
        for line in text.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


async def _drain_stream(response):
    """收集流式响应内容：async 生成器响应 async 迭代；同步生成器响应同步迭代
    （Django 5.2 对 sync 迭代器的 streaming_content 无 __aiter__）。"""
    chunks = []
    if hasattr(response.streaming_content, "__aiter__"):
        async for chunk in response.streaming_content:
            chunks.append(chunk)
    else:
        # 同步迭代器（如缓存命中合成帧）：经 sync_to_async 离开事件循环迭代
        await asyncio.to_thread(lambda: chunks.extend(list(response.streaming_content)))
    return chunks


class StreamEndpointTests(SimpleTestCase):
    """C-8 + RP-01 流式契约：事件序列保持、缓存共用、空索引语义对齐、泵线程出帧。"""

    def _fake_stream_events(self, delay=0.0, degraded=True):
        events = [
            {"type": "chunk", "content": "回答"},
        ]
        if degraded:
            events.append({"type": "degradation", "message": "降级提示"})
        events.append({"type": "sources", "data": [{"content": "片段", "source": "f.md", "metadata": {}}]})
        if delay:

            def _gen():
                for evt in events:
                    time.sleep(delay)
                    yield evt

            return _gen()
        return iter(events)

    def _stream_request(self):
        return _query_request("/api/v1/knowledge/query/stream/")

    def test_stream_event_sequence_preserved_and_cache_written(self):
        """正常流：事件序列 start→chunk→sources→end 保持，且完成写回查询缓存。"""
        cache_write = mock.MagicMock()
        manager = _FakeIndexManager()
        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.cache_query_result", cache_write),
            mock.patch(f"{_VIEWS_RAG}.build_user_retriever_bundle", return_value=_fake_bundle()),
            mock.patch(
                f"{_VIEWS_RAG}.stream_strict_rag",
                return_value=self._fake_stream_events(delay=0.02, degraded=False),
            ),
        ):
            response = vr.rag_query_stream(self._stream_request())
            self.assertEqual(response.status_code, 200)
            chunks = asyncio.run(_drain_stream(response))

        types = [e["type"] for e in _collect_sse_events(chunks)]
        self.assertEqual(types, ["start", "chunk", "sources", "end"])
        # C-8：流式完成写回与非流式共享的查询缓存（修复前流式零缓存读写）
        cache_write.assert_called_once()
        cached_result = cache_write.call_args.args[1]
        self.assertEqual(cached_result["answer"], "回答")
        self.assertNotIn("degraded", cached_result)

    def test_stream_degraded_result_not_cached(self):
        """Task 4/B3：降级流（degradation 事件）不写查询缓存，事件序列照常送达。"""
        cache_write = mock.MagicMock()
        manager = _FakeIndexManager()
        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.cache_query_result", cache_write),
            mock.patch(f"{_VIEWS_RAG}.build_user_retriever_bundle", return_value=_fake_bundle()),
            mock.patch(f"{_VIEWS_RAG}.stream_strict_rag", return_value=self._fake_stream_events(delay=0.02)),
        ):
            response = vr.rag_query_stream(self._stream_request())
            self.assertEqual(response.status_code, 200)
            chunks = asyncio.run(_drain_stream(response))

        types = [e["type"] for e in _collect_sse_events(chunks)]
        self.assertEqual(types, ["start", "chunk", "degradation", "sources", "end"])
        cache_write.assert_not_called()

    def test_stream_empty_answer_not_cached(self):
        """Task 4/B3：空答案流不写查询缓存（受控空应答不污染缓存）。"""
        cache_write = mock.MagicMock()
        manager = _FakeIndexManager()
        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.cache_query_result", cache_write),
            mock.patch(f"{_VIEWS_RAG}.build_user_retriever_bundle", return_value=_fake_bundle()),
            mock.patch(f"{_VIEWS_RAG}.stream_strict_rag", return_value=iter([])),
        ):
            response = vr.rag_query_stream(self._stream_request())
            chunks = asyncio.run(_drain_stream(response))

        types = [e["type"] for e in _collect_sse_events(chunks)]
        self.assertEqual(types, ["start", "end"])
        cache_write.assert_not_called()

    def test_stream_cache_hit_synthesizes_event_sequence(self):
        """C-8：流式命中查询缓存 → 合成 start→chunk→degradation→sources→end，零检索。"""
        cached = {
            "result": {
                "answer": "缓存答案",
                "sources": [{"content": "片段", "source": "f.md", "metadata": {}}],
                "success": True,
                "degraded": True,
                "degradation_notice": "降级提示",
            }
        }
        with (
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=cached),
            mock.patch(f"{_VIEWS_RAG}.IndexManager") as manager_cls,
        ):
            response = vr.rag_query_stream(self._stream_request())
            chunks = asyncio.run(_drain_stream(response))

        # 命中缓存不触检索器构建（前置的 index_exists/load_metadata 归属校验仍保留）
        manager = manager_cls.return_value
        manager.load_index.assert_not_called()
        types = [e["type"] for e in _collect_sse_events(chunks)]
        self.assertEqual(types, ["start", "chunk", "degradation", "sources", "end"])

    def test_stream_empty_index_rejected_before_stream(self):
        """C-8：空索引语义两链对齐——流式同样 400 拒绝（修复前走 NO_RESULT_ANSWER 生成链）。"""
        manager = _FakeIndexManager(metadata=_fake_metadata(num_documents=0))
        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
        ):
            response = vr.rag_query_stream(self._stream_request())

        self.assertEqual(response.status_code, 400)
        self.assertIn("索引中暂无文档", response.data["message"])

    def test_stream_pump_yields_incrementally(self):
        """RP-01 泵机制：同步生成器在专用线程迭代，事件逐个即时投递（非末端一次吐出）。"""
        manager = _FakeIndexManager()

        def _slow_events():
            for evt in self._fake_stream_events():
                time.sleep(0.05)
                yield evt

        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.get_cached_query", return_value=None),
            mock.patch(f"{_VIEWS_RAG}.QueryCacheService.cache_query_result"),
            mock.patch(f"{_VIEWS_RAG}.build_user_retriever_bundle", return_value=_fake_bundle()),
            mock.patch(f"{_VIEWS_RAG}.stream_strict_rag", return_value=_slow_events()),
        ):
            response = vr.rag_query_stream(self._stream_request())
            chunks = asyncio.run(_drain_stream(response))

        types = [e["type"] for e in _collect_sse_events(chunks)]
        self.assertEqual(types, ["start", "chunk", "degradation", "sources", "end"])
