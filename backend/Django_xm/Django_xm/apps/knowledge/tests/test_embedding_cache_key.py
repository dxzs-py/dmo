"""RP-06 + RP-07：embedding 响应缓存键 provider/维度隔离与设置路径两层缓存失效。

修复前缺口（rag_audit_perf RP-06/RP-07）：
- RP-06：embedding Redis 响应缓存键 `embedding:{model}:{text_hash}` 的 model 实参恒
  "default"，不含 provider/维度——切换 embedding provider 后键不变，查询侧命中旧
  provider 向量最长 7 天（维度不同 → 自愈循环反复失败；维度相同 → 检索质量静默劣化）；
- RP-07：settings_views 重建/切换路径不失效两层查询缓存（rag_query:*/vector_search:*
  残留旧 provider 时代答案，对比 kb_service 5 处均失效）。

修复形态：
- RP-06：CachedEmbeddings 缓存键命名空间 = 活跃 provider id（每次调用动态取
  get_active_provider_id，fallback 切换后随之生效）+ 显式 required_dimension 维度段；
  settings_views 切换分支追加 delete_pattern("embedding:*") 清存量键（纵深防御）；
- RP-07：RebuildIndexesView 重建循环后对每个成功 name 调
  invalidate_knowledge_cache（对齐 kb_service.rebuild_index_from_source_files）。

放置说明：视图接线用例随 AISettingsView/RebuildIndexesView 自
ai_engine/tests 迁入 knowledge/tests（环 2 破除任务 4.2，用例内容零修改）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_embedding_cache_key
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

_CACHE_SVC = "Django_xm.apps.cache_manager.services.cache_service"
_EMB_SERVICE = "Django_xm.apps.knowledge.services.embedding_service"
_KB_CONFIG = "Django_xm.apps.knowledge.config"
_VIEWS = "Django_xm.apps.knowledge.views_settings"
_PGV = "Django_xm.apps.knowledge.vector_store.pgvector_backend"


class _BareEmbeddings:
    """无 provider 感知的裸 Embeddings 桩（无 get_active_provider_id 方法）。"""

    def embed_query(self, text):
        return [0.0]

    def embed_documents(self, texts):
        return [[0.0]]


class _ProviderAwareEmbeddings:
    """带活跃 provider id 的 Embeddings 桩（FallbackEmbedding 形态）。"""

    def __init__(self, provider_ids):
        self._provider_ids = list(provider_ids)
        self._calls = 0

    def get_active_provider_id(self):
        pid = self._provider_ids[min(self._calls, len(self._provider_ids) - 1)]
        self._calls += 1
        return pid

    def embed_query(self, text):
        return [0.0]

    def embed_documents(self, texts):
        return [[0.0]]


class CachedEmbeddingsCacheKeyTests(SimpleTestCase):
    """RP-06：缓存键命名空间含活跃 provider id 与维度段。"""

    def _make(self, inner, dimension=None):
        from Django_xm.apps.knowledge.services.embedding_service import CachedEmbeddings

        return CachedEmbeddings(inner, model="default", dimension=dimension)

    def test_key_contains_active_provider_id(self):
        cached = self._make(_ProviderAwareEmbeddings(["openai"]))
        with mock.patch(f"{_CACHE_SVC}.VectorSearchCacheService.get_cached_embedding") as get_cached:
            cached.embed_query("hello")

        get_cached.assert_called_once_with("hello", "openai")

    def test_key_includes_dimension_segment(self):
        cached = self._make(_ProviderAwareEmbeddings(["openai"]), dimension=1536)
        with mock.patch(f"{_CACHE_SVC}.VectorSearchCacheService.get_cached_embedding") as get_cached:
            cached.embed_query("hello")

        get_cached.assert_called_once_with("hello", "openai:d1536")

    def test_no_dimension_segment_when_none(self):
        cached = self._make(_ProviderAwareEmbeddings(["openai"]), dimension=None)
        with mock.patch(f"{_CACHE_SVC}.VectorSearchCacheService.get_cached_embedding") as get_cached:
            cached.embed_query("hello")

        self.assertEqual(get_cached.call_args.args[1], "openai")
        self.assertNotIn("dNone", get_cached.call_args.args[1])

    def test_bare_embeddings_fall_back_to_model(self):
        """裸 Embeddings（无 provider 感知）回退注入的 model 命名空间。"""
        cached = self._make(_BareEmbeddings(), dimension=None)
        with mock.patch(f"{_CACHE_SVC}.VectorSearchCacheService.get_cached_embedding") as get_cached:
            cached.embed_query("hello")

        get_cached.assert_called_once_with("hello", "default")

    def test_provider_switch_changes_cache_key_dynamically(self):
        """fallback 切换后同一实例的键随活跃 provider 变化（写读均隔离到新命名空间）。"""
        cached = self._make(_ProviderAwareEmbeddings(["openai", "baidu_qianfan"]))
        with (
            mock.patch(f"{_CACHE_SVC}.VectorSearchCacheService.get_cached_embedding", return_value=None) as get_cached,
            mock.patch(f"{_CACHE_SVC}.VectorSearchCacheService.cache_embedding") as set_cached,
        ):
            cached.embed_query("hello")
            cached.embed_query("hello")

        self.assertEqual(get_cached.call_args_list[0].args[1], "openai")
        self.assertEqual(get_cached.call_args_list[1].args[1], "baidu_qianfan")
        # cache_embedding(text, vector, model)：model 在 args[2]
        self.assertEqual(set_cached.call_args_list[-1].args[2], "baidu_qianfan")

    def test_batch_documents_use_same_namespace(self):
        """embed_documents 批量读写使用同一 provider 命名空间（RP-11 起为批量接口）。"""
        cached = self._make(_ProviderAwareEmbeddings(["openai"]))
        with (
            mock.patch(
                f"{_CACHE_SVC}.VectorSearchCacheService.get_cached_embeddings_batch", return_value={}
            ) as get_cached,
            mock.patch(f"{_CACHE_SVC}.VectorSearchCacheService.cache_embeddings_batch") as set_cached,
        ):
            cached.embed_documents(["a", "b"])

        # 批量读：单次调用携带全量文本与命名空间
        get_cached.assert_called_once_with(["a", "b"], "openai")
        # 批量写：items 为 (text, vector) 元组列表，model 在 args[1]
        self.assertTrue(set_cached.call_args_list)
        for call in set_cached.call_args_list:
            self.assertEqual(call.args[1], "openai")


class EmbeddingBatchCacheIOTests(SimpleTestCase):
    """RP-11：embed_documents 缓存读写批量化（单次 Redis 往返替代逐条）。"""

    def _make_wrapper(self):
        from Django_xm.apps.knowledge.services.embedding_service import CachedEmbeddings

        inner = mock.MagicMock()
        # 按请求文本长度产出向量（模拟真实按批返回，与请求文本一一对应）
        inner.embed_documents.side_effect = lambda texts: [[float(len(t))] for t in texts]
        inner.get_active_provider_id = mock.MagicMock(return_value="openai")
        return CachedEmbeddings(inner, model="default")

    def test_batch_read_and_write_roundtrip(self):
        """未命中批量写 / 命中批量读：结果顺序保持，未命中项由底层向量填充。"""
        wrapper = self._make_wrapper()
        with (
            mock.patch(
                f"{_CACHE_SVC}.VectorSearchCacheService.get_cached_embeddings_batch",
                return_value={"cached-text": [9.9]},
            ) as get_batch,
            mock.patch(f"{_CACHE_SVC}.VectorSearchCacheService.cache_embeddings_batch") as set_batch,
        ):
            results = wrapper.embed_documents(["cached-text", "aa", "bbb"])

        get_batch.assert_called_once_with(["cached-text", "aa", "bbb"], "openai")
        set_batch.assert_called_once()
        items, model = set_batch.call_args.args
        self.assertEqual(model, "openai")
        self.assertEqual(dict(items), {"aa": [2.0], "bbb": [3.0]})
        self.assertEqual(results, [[9.9], [2.0], [3.0]])
        self.assertEqual(wrapper.cache_stats["hit"], 1)
        self.assertEqual(wrapper.cache_stats["miss"], 2)

    def test_cache_service_batch_delegates_to_django_cache(self):
        """CacheService.get_many/set_many 经 django cache 批量接口单次往返。"""
        from Django_xm.apps.cache_manager.services import cache_service as cs

        with (
            mock.patch.object(cs.cache, "get_many", return_value={"k1": "v"}) as get_many,
            mock.patch.object(cs.cache, "set_many", return_value=[]) as set_many,
        ):
            found = cs.CacheService.get_many(["k1", "k2"])
            written = cs.CacheService.set_many({"k1": "v"}, ttl=60)

        get_many.assert_called_once_with(["k1", "k2"])
        set_many.assert_called_once()
        self.assertEqual(written, 1)
        self.assertEqual(found, {"k1": "v"})


class GetEmbeddingsWiringTests(SimpleTestCase):
    """get_embeddings 组装点：cache_wrapper_factory 注入 required_dimension。"""

    def test_factory_passes_required_dimension(self):
        from Django_xm.apps.knowledge.services import embedding_service as es

        captured = {}

        def _fake_fallback(**kwargs):
            captured.update(kwargs)
            factory = kwargs["cache_wrapper_factory"]
            inner = _ProviderAwareEmbeddings(["openai"])
            wrapper = factory(inner)
            captured["wrapper"] = wrapper
            return wrapper

        with (
            mock.patch(f"{_EMB_SERVICE}.get_embeddings_with_fallback", side_effect=_fake_fallback),
            mock.patch(f"{_KB_CONFIG}.get_system_embedding_provider", return_value=None),
            mock.patch(f"{_KB_CONFIG}.settings") as _settings,
        ):
            _settings.embedding_batch_size = 16
            wrapper = es.get_embeddings(required_dimension=1024)

        self.assertIsInstance(captured["wrapper"], es.CachedEmbeddings)
        self.assertEqual(captured["wrapper"]._dimension, 1024)
        self.assertEqual(captured["wrapper"]._model, "default")


class SettingsSwitchInvalidationTests(SimpleTestCase):
    """RP-06 切换清键 + RP-07 重建失效（视图接线级）。"""

    def _settings_view_request(self, payload):
        """构造 DRF Request（直调视图方法绕过 dispatch，需手工注入 user）。"""
        from rest_framework.parsers import JSONParser
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        django_request = factory.post("/api/v1/ai/settings/", payload, format="json")
        request = Request(django_request, parsers=[JSONParser()])
        request.user = SimpleNamespace(is_authenticated=True, id=1, pk=1, is_staff=True)
        return request

    def test_provider_switch_clears_embedding_cache_keys(self):
        """RP-06：切换 embedding provider 后 delete_pattern("embedding:*") 被调用。"""
        from Django_xm.apps.knowledge.views_settings import AISettingsView

        registry = [
            {"id": "openai", "label": "OpenAI", "dimension": 1536, "min_dimension": 0, "native_max_dimension": 0},
            {
                "id": "baidu_qianfan",
                "label": "Qianfan",
                "dimension": 1536,
                "min_dimension": 0,
                "native_max_dimension": 0,
            },
        ]
        with (
            mock.patch(f"{_VIEWS}.SystemConfig") as config_cls,
            mock.patch(f"{_VIEWS}.get_embedding_registry", return_value=registry),
            mock.patch(f"{_VIEWS}.get_embedding_provider_ids", return_value={"openai", "baidu_qianfan"}),
            mock.patch(f"{_VIEWS}.get_embedding_fallback_chain", return_value=registry),
            mock.patch(f"{_VIEWS}.CacheService.delete_pattern") as delete_pattern,
        ):
            config_cls.get_value.return_value = {"provider_id": "openai"}
            response = AISettingsView().put(
                self._settings_view_request({"embedding_provider": {"provider_id": "baidu_qianfan"}})
            )

        self.assertEqual(response.status_code, 200)
        delete_pattern.assert_called_once_with("embedding:*")

    def test_rebuild_invalidates_query_cache_per_index(self):
        """RP-07：重建循环完成后对每个成功 name 调 invalidate_knowledge_cache。"""
        from Django_xm.apps.knowledge.views_settings import RebuildIndexesView

        registry = [{"id": "openai", "label": "OpenAI", "dimension": 1536}]
        manager = mock.MagicMock(name="IndexManager")
        manager.list_indexes.return_value = [
            {"name": "user_1_kb1", "num_documents": 2, "embedding_dimension": 768, "store_type": "pgvector"},
            {"name": "user_2_kb2", "num_documents": 1, "embedding_dimension": 768, "store_type": "pgvector"},
        ]
        backend = mock.MagicMock(name="pgvector_backend")
        # pgvector 分支经 read_all_documents 读取文档（非空才进入重建循环）
        backend.read_all_documents.return_value = [
            SimpleNamespace(page_content="片段", metadata={}),
        ]
        manager.get_backend.return_value = backend
        backend.get_embedding_column_dimension.return_value = 1536

        def _fake_create_index(name, **kwargs):
            return None

        manager.create_index.side_effect = _fake_create_index

        with (
            mock.patch(f"{_VIEWS}.get_index_manager", return_value=manager),
            mock.patch(f"{_VIEWS}.get_embedding_registry", return_value=registry),
            mock.patch(f"{_VIEWS}.get_embedding_provider_ids", return_value={"openai"}),
            mock.patch(f"{_EMB_SERVICE}.get_embeddings", return_value=mock.MagicMock()),
            # settings_views 重建段内自建 PGVectorBackend（维度检查），拦截其 DB 访问
            mock.patch(f"{_PGV}.PGVectorBackend") as backend_cls,
            mock.patch(f"{_VIEWS}.invalidate_knowledge_cache") as invalidate,
        ):
            backend_cls.return_value.get_embedding_column_dimension.return_value = 1536
            response = RebuildIndexesView().post(
                self._settings_view_request({"provider_id": "openai", "index_names": ["user_1_kb1", "user_2_kb2"]})
            )

        self.assertEqual(response.status_code, 200)
        invalidated = [call.kwargs["user_index_name"] for call in invalidate.call_args_list]
        self.assertEqual(invalidated, ["user_1_kb1", "user_2_kb2"])
        for call in invalidate.call_args_list:
            self.assertEqual(call.kwargs["user_id"], 1)


class _AlwaysFail:
    """恒失败的 stub provider（模拟主 provider 宕机）。"""

    def embed_query(self, text):
        raise ConnectionError("primary provider down")

    def embed_documents(self, texts):
        raise ConnectionError("primary provider down")

    async def aembed_query(self, text):
        raise ConnectionError("primary provider down")

    async def aembed_documents(self, texts):
        raise ConnectionError("primary provider down")


class _AlwaysOk:
    """恒成功的 stub provider（备用 provider）。"""

    def embed_query(self, text):
        return [0.0] * 4

    def embed_documents(self, texts):
        return [[0.0] * 4 for _ in texts]

    async def aembed_query(self, text):
        return [0.0] * 4

    async def aembed_documents(self, texts):
        return [[0.0] * 4 for _ in texts]


class CachedEmbeddingsPassThroughTests(SimpleTestCase):
    """kb_service 消费点透传链：CachedEmbeddings.get_fallback_events 读到全量历史。

    （自 ai_engine/tests/test_fallback_events_append_only.py 迁入——环 2 破除
    任务 4.2，消 ai_engine tests → knowledge 测试放置边，用例内容零修改。）
    """

    def test_cached_wrapper_forwards_events(self):
        from Django_xm.apps.ai_engine.services.embedding_factory import FallbackEmbedding
        from Django_xm.apps.knowledge.services.embedding_service import CachedEmbeddings

        fb = FallbackEmbedding(
            [_AlwaysFail(), _AlwaysOk()],
            ["Primary", "Backup"],
            provider_ids=["p1", "p2"],
        )
        wrapped = CachedEmbeddings(fb, model="default")
        with mock.patch(
            "Django_xm.apps.cache_manager.services.cache_service.VectorSearchCacheService.get_cached_embedding",
            return_value=None,
        ), mock.patch(
            "Django_xm.apps.cache_manager.services.cache_service.VectorSearchCacheService.cache_embedding"
        ):
            wrapped.embed_query("x")  # 经缓存层触发底层切换

        events = wrapped.get_fallback_events()
        self.assertEqual(len(events), 1, "消费点（kb_service 上传链）经 CachedEmbeddings 读到降级事件")


if __name__ == "__main__":
    unittest.main()
