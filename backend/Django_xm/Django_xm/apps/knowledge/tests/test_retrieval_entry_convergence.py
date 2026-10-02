"""RAG 检索入口收敛表征测试（ARCH-08 / §5.1 B⑨）。

在收敛前锁定 3 处检索入口的现有外部行为，收敛（kb_service.search_knowledge_base
与 views_rag.RAGSearchView 内部改调 UnifiedRagPipeline.search_scored）后必须保持全绿：

- kb_service.search_knowledge_base（同步服务层入口）：
  * 空查询 → ValueError；知识库不存在 → FileNotFoundError
  * 正常路径：返回 [{content, source, score}]（score 为 float，None 归 0），
    并写入 VectorSearchCacheService
  * 缓存命中：直接返回缓存，不触碰向量库
  * 维度不匹配（"different vector dimensions"）：自愈重试——释放 engine、
    关闭 Django 连接、reset_pgvector_cache(dimension)、清 IndexManager._cache、
    重建向量库后重检索一次；非维度异常原样上抛
- views_rag.RAGSearchView（API 入口）：
  * 正常路径：[{content, metadata, score}]（score 为 None 时经序列化器输出 null），
    score_threshold 在检索后过滤生效
  * 缓存命中：直接返回缓存（不检查索引存在性）
  * 索引不存在 → NotFound；索引无文档 → ValidationError
- 检索核心统一后仍经 vector_store.similarity_search_with_score（表征测试
  打桩在向量库方法层，不锁模块内函数绑定，mock 点跨收敛前后均有效）

strict_rag_chain 与 learning/nodes/retrieval_node 的检索行为已分别由
test_strict_rag_convergence.py 与 learning/tests/test_retrieval_node.py 锁定。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_retrieval_entry_convergence --noinput
（纯单元测试，无 DB / Redis / LLM 依赖；manage.py test 需 DB_NAME 指向独立测试库）
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.db import connections
from rest_framework.exceptions import NotFound, ValidationError

from Django_xm.apps.knowledge.services import kb_service
from Django_xm.apps.knowledge.services.kb_service import search_knowledge_base
from Django_xm.apps.knowledge.views_rag import RAGSearchView
from Django_xm.apps.knowledge.vector_store.pgvector_runtime import reset_pgvector_cache

_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"
_VIEWS_RAG = "Django_xm.apps.knowledge.views_rag"
_PGV_RUNTIME = "Django_xm.apps.knowledge.vector_store.pgvector_runtime"


def _make_doc(content: str, source: str):
    from langchain_core.documents import Document

    return Document(page_content=content, metadata={"source": source, "original_name": source})


class _FakeUser:
    id = 1


class _FakeRequest:
    """最小请求替身：视图仅访问 request.data 与 request.user（权限由 dispatch 层承担）。"""

    def __init__(self, data: dict):
        self.data = data
        self.user = _FakeUser()


class SearchKnowledgeBaseTests(unittest.TestCase):
    """kb_service.search_knowledge_base 表征：返回结构 / 缓存 / 自愈重试 / 异常契约。"""

    def _patch_common(self, cached=None):
        """打桩 kb_service 模块绑定：IndexManager（类 mock）/ get_embeddings / 缓存服务。"""
        store = mock.MagicMock(name="vector_store")
        store.similarity_search_with_score.return_value = [
            (_make_doc("片段一", "a.md"), 0.92),
            (_make_doc("片段二", "b.md"), None),
        ]

        manager_cls = mock.MagicMock(name="IndexManager")
        manager = manager_cls.return_value
        manager.index_exists.return_value = True
        manager._load_metadata.return_value = {"embedding_dimension": 1024, "num_documents": 3}
        manager.load_index.return_value = store

        embeddings = mock.MagicMock(name="embeddings")

        cache_svc = mock.MagicMock(name="VectorSearchCacheService")
        cache_svc.get_cached_search.return_value = cached

        patchers = [
            mock.patch(f"{_KB_SERVICE}.IndexManager", manager_cls),
            mock.patch(f"{_KB_SERVICE}.get_embeddings", return_value=embeddings),
            mock.patch(f"{_KB_SERVICE}.VectorSearchCacheService", cache_svc),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return manager, store, cache_svc

    def test_empty_query_raises_value_error(self):
        with self.assertRaises(ValueError):
            search_knowledge_base(_FakeUser(), "kb1", "")

    def test_index_missing_raises_file_not_found(self):
        manager, store, _ = self._patch_common()
        manager.index_exists.return_value = False

        with self.assertRaises(FileNotFoundError):
            search_knowledge_base(_FakeUser(), "kb1", "查询")

        store.similarity_search_with_score.assert_not_called()

    def test_normal_path_returns_flat_items_and_caches(self):
        manager, store, cache_svc = self._patch_common()

        results = search_knowledge_base(_FakeUser(), "kb1", "查询", top_k=3)

        # 扁平结构：content / source / score（None 归 0）
        self.assertEqual(
            results,
            [
                {"content": "片段一", "source": "a.md", "score": 0.92},
                {"content": "片段二", "source": "b.md", "score": 0},
            ],
        )
        for item in results:
            self.assertIsInstance(item["score"], (int, float))
        # 写入缓存（key 含 user 前缀索引名与 top_k）
        cache_svc.cache_search_result.assert_called_once()
        self.assertEqual(cache_svc.cache_search_result.call_args.args[0], "查询")
        self.assertEqual(cache_svc.cache_search_result.call_args.args[2], "user_1_kb1")
        self.assertEqual(cache_svc.cache_search_result.call_args.args[3], 3)

    def test_cache_hit_short_circuits_vector_store(self):
        cached = [{"content": "缓存片段", "source": "c.md", "score": 0.5}]
        manager, store, cache_svc = self._patch_common(cached=cached)

        results = search_knowledge_base(_FakeUser(), "kb1", "查询")

        self.assertEqual(results, cached)
        store.similarity_search_with_score.assert_not_called()
        cache_svc.cache_search_result.assert_not_called()

    def test_dimension_mismatch_triggers_self_heal_retry(self):
        """维度不匹配：清理 pgvector 运行时缓存并重建向量库后重试一次。"""
        bad_store = mock.MagicMock(name="bad_store")
        bad_store.similarity_search_with_score.side_effect = RuntimeError(
            "different vector dimensions: 768 != 1024"
        )
        good_store = mock.MagicMock(name="good_store")
        good_store.similarity_search_with_score.return_value = [
            (_make_doc("重试片段", "r.md"), 0.8),
        ]

        manager, _, cache_svc = self._patch_common()
        manager.load_index.side_effect = [bad_store, good_store]

        with (
            mock.patch.object(connections, "close_all") as close_all,
            mock.patch(f"{_PGV_RUNTIME}.reset_pgvector_cache") as reset_cache,
        ):
            results = search_knowledge_base(_FakeUser(), "kb1", "查询")

        self.assertEqual(
            results,
            [{"content": "重试片段", "source": "r.md", "score": 0.8}],
        )
        bad_store.similarity_search_with_score.assert_called_once()
        good_store.similarity_search_with_score.assert_called_once()
        bad_store._engine.dispose.assert_called_once()
        close_all.assert_called_once()
        reset_cache.assert_called_once()
        # 自愈重试以索引元数据中的维度为参数
        self.assertEqual(reset_cache.call_args.kwargs.get("dimension"), 1024)
        cache_svc.cache_search_result.assert_called_once()

    def test_non_dimension_error_propagates(self):
        manager, store, _ = self._patch_common()
        store.similarity_search_with_score.side_effect = ValueError("boom")

        with self.assertRaises(ValueError):
            search_knowledge_base(_FakeUser(), "kb1", "查询")


class RAGSearchViewTests(unittest.TestCase):
    """views_rag.RAGSearchView 表征：响应结构 / 阈值过滤 / 缓存 / 校验分支。"""

    def _post(self, data: dict, cached=None, store_results=None):
        """打桩视图模块绑定后调用 post，返回 (response, store, cache_svc)。

        store_results 可选：预先配置 similarity_search_with_score 的返回值。
        """
        store = mock.MagicMock(name="vector_store")
        store.similarity_search_with_score.return_value = store_results or [
            (_make_doc("高分片段", "a.md"), 0.9),
            (_make_doc("低分片段", "b.md"), 0.1),
        ]

        manager = mock.MagicMock(name="IndexManager")
        manager.index_exists.return_value = True
        manager._load_metadata.return_value = {"embedding_dimension": 1024, "num_documents": 5}
        manager.load_index.return_value = store

        cache_svc = mock.MagicMock(name="VectorSearchCacheService")
        cache_svc.get_cached_search.return_value = cached

        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.get_embeddings", return_value=mock.MagicMock()),
            mock.patch(f"{_VIEWS_RAG}.VectorSearchCacheService", cache_svc),
        ):
            response = RAGSearchView().post(_FakeRequest(data))

        return response, store, cache_svc

    def test_normal_path_returns_metadata_items(self):
        response, store, cache_svc = self._post({"index_name": "kb1", "query": "查询", "k": 4})

        data = response.data["data"]
        # 对外响应结构：content / metadata / score
        self.assertEqual(
            data,
            [
                {"content": "高分片段", "metadata": {"source": "a.md", "original_name": "a.md"}, "score": 0.9},
                {"content": "低分片段", "metadata": {"source": "b.md", "original_name": "b.md"}, "score": 0.1},
            ],
        )
        # 检索参数透传：k 作用于向量库调用
        store.similarity_search_with_score.assert_called_once()
        self.assertEqual(store.similarity_search_with_score.call_args.kwargs.get("k"), 4)
        cache_svc.cache_search_result.assert_called_once()

    def test_score_threshold_filters_results(self):
        response, store, _ = self._post(
            {"index_name": "kb1", "query": "查询", "k": 4, "score_threshold": 0.5}
        )

        data = response.data["data"]
        self.assertEqual([item["content"] for item in data], ["高分片段"])
        store.similarity_search_with_score.assert_called_once()

    def test_score_none_serializes_as_null(self):
        """score 为 None：视图原始条目省略 score 键，经 SearchResultSerializer(allow_null) 输出 null。"""
        response, _, _ = self._post(
            {"index_name": "kb1", "query": "查询", "k": 4},
            store_results=[(_make_doc("无分片段", "n.md"), None)],
        )
        data = response.data["data"]
        self.assertEqual(
            data,
            [{"content": "无分片段", "metadata": {"source": "n.md", "original_name": "n.md"}, "score": None}],
        )

    def test_cache_hit_returns_cached_without_index_access(self):
        cached = [{"content": "缓存片段", "metadata": {"source": "c.md"}, "score": 0.66}]
        response, store, cache_svc = self._post(
            {"index_name": "kb1", "query": "查询", "k": 4}, cached=cached
        )

        self.assertEqual(response.data["data"], cached)
        store.similarity_search_with_score.assert_not_called()
        cache_svc.cache_search_result.assert_not_called()

    def test_index_missing_raises_not_found(self):
        manager = mock.MagicMock()
        manager.index_exists.return_value = False

        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.VectorSearchCacheService") as cache_svc,
        ):
            cache_svc.get_cached_search.return_value = None
            with self.assertRaises(NotFound):
                RAGSearchView().post(_FakeRequest({"index_name": "ghost", "query": "查询", "k": 4}))

    def test_empty_index_raises_validation_error(self):
        manager = mock.MagicMock()
        manager.index_exists.return_value = True
        manager._load_metadata.return_value = {"embedding_dimension": 1024, "num_documents": 0}

        with (
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.VectorSearchCacheService") as cache_svc,
        ):
            cache_svc.get_cached_search.return_value = None
            with self.assertRaises(ValidationError):
                RAGSearchView().post(_FakeRequest({"index_name": "kb1", "query": "查询", "k": 4}))


if __name__ == "__main__":
    unittest.main()
