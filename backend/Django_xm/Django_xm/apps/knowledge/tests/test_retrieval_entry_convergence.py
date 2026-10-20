"""RAG 检索入口收敛表征测试（ARCH-08 / §5.1 B⑨）。

锁定检索入口收敛（kb_service.search_knowledge_base 内部改调
UnifiedRagPipeline.search_scored）后的现有外部行为：

- kb_service.search_knowledge_base（同步服务层入口）：
  * 空查询 → ValueError；知识库不存在 → FileNotFoundError
  * 正常路径：返回 [{content, source, score}]（score 为 float，None 归 0），
    并写入 VectorSearchCacheService
  * 缓存命中：直接返回缓存，不触碰向量库
  * 维度不匹配（"different vector dimensions"）：自愈重试——释放 engine、
    关闭 Django 连接、reset_pgvector_cache(dimension)、清 IndexManager._cache、
    重建向量库后重检索一次；非维度异常原样上抛
- 检索核心统一后仍经 vector_store.similarity_search_with_score（表征测试
  打桩在向量库方法层，不锁模块内函数绑定，mock 点跨收敛前后均有效）

原单库检索 API 入口（views_rag）已随死端点清理删除。
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

from Django_xm.apps.knowledge.config import settings as app_cfg
from Django_xm.apps.knowledge.services.kb_service import search_knowledge_base

_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"
_RETRIEVAL_SERVICE = "Django_xm.apps.knowledge.services.retrieval_service"
_PGV_RUNTIME = "Django_xm.apps.knowledge.vector_store.pgvector_runtime"


def _make_doc(content: str, source: str):
    from langchain_core.documents import Document

    return Document(page_content=content, metadata={"source": source, "original_name": source})


class _FakeUser:
    id = 1


class SearchKnowledgeBaseTests(unittest.TestCase):
    """kb_service.search_knowledge_base 表征：返回结构 / 缓存 / 自愈重试 / 异常契约。"""

    def _patch_common(self, cached=None, store=None, reload_store=None):
        """打桩：load_user_vector_store 门面（B-1 后 kb_service 不再内联构建链）
        + 缓存服务。向量库打桩在 similarity_search_with_score 方法层。

        3.2：kb_service 主链路接入 rag_score_threshold（锁定 0.5 便于阈值断言）。

        Args:
            store: 主向量库 mock（默认生成，返回 [(片段一,0.92),(片段二,None)]）
            reload_store: 自愈重建回调返回的向量库（默认与主 store 相同）
        """
        threshold_patch = mock.patch.object(app_cfg, "rag_score_threshold", 0.5)
        threshold_patch.start()
        self.addCleanup(threshold_patch.stop)

        if store is None:
            store = mock.MagicMock(name="vector_store")
            store.similarity_search_with_score.return_value = [
                (_make_doc("片段一", "a.md"), 0.92),
                (_make_doc("片段二", "b.md"), None),
            ]

        manager_cls = mock.MagicMock(name="IndexManager")
        manager = manager_cls.return_value
        manager.index_exists.return_value = True
        manager.get_score_semantics.return_value = "similarity"

        reload_vector_store = mock.MagicMock(name="reload_vector_store", return_value=reload_store or store)
        loaded = (store, {"embedding_dimension": 1024, "num_documents": 3}, 1024, reload_vector_store)
        load_user_vector_store = mock.MagicMock(name="load_user_vector_store", return_value=loaded)

        cache_svc = mock.MagicMock(name="VectorSearchCacheService")
        cache_svc.get_cached_search.return_value = cached

        patchers = [
            mock.patch(f"{_KB_SERVICE}.IndexManager", manager_cls),
            mock.patch(f"{_RETRIEVAL_SERVICE}.load_user_vector_store", load_user_vector_store),
            mock.patch(f"{_KB_SERVICE}.VectorSearchCacheService", cache_svc),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        return manager, store, cache_svc, reload_vector_store, load_user_vector_store

    def test_empty_query_raises_value_error(self):
        with self.assertRaises(ValueError):
            search_knowledge_base(_FakeUser(), "kb1", "")

    def test_index_missing_raises_file_not_found(self):
        manager, store, _, _, _ = self._patch_common()
        manager.index_exists.return_value = False

        with self.assertRaises(FileNotFoundError):
            search_knowledge_base(_FakeUser(), "kb1", "查询")

        store.similarity_search_with_score.assert_not_called()

    def test_normal_path_returns_flat_items_and_caches(self):
        _manager, _store, cache_svc, _, _ = self._patch_common()

        results = search_knowledge_base(_FakeUser(), "kb1", "查询", top_k=3)

        # 扁平结构：content / source / score
        # 3.2：无分数片段（后端未返回 score）在阈值过滤下被剔除，
        # 不再"None 归 0"冒充检索命中
        self.assertEqual(
            results,
            [{"content": "片段一", "source": "a.md", "score": 0.92}],
        )
        for item in results:
            self.assertIsInstance(item["score"], (int, float))
        # 写入缓存（key 含 user 前缀索引名与 top_k）
        cache_svc.cache_search_result.assert_called_once()
        self.assertEqual(cache_svc.cache_search_result.call_args.args[0], "查询")
        self.assertEqual(cache_svc.cache_search_result.call_args.args[2], "user_1_kb1")
        self.assertEqual(cache_svc.cache_search_result.call_args.args[3], 3)

    def test_low_score_documents_filtered_by_threshold(self):
        """3.2：低于 rag_score_threshold 的低分块被过滤（归一相似度口径）。"""
        store = mock.MagicMock(name="vector_store")
        store.similarity_search_with_score.return_value = [
            (_make_doc("高分片段", "a.md"), 0.92),
            (_make_doc("低分片段", "b.md"), 0.3),
        ]

        self._patch_common(store=store)

        results = search_knowledge_base(_FakeUser(), "kb1", "查询", top_k=5)

        self.assertEqual([item["content"] for item in results], ["高分片段"])

    def test_cache_hit_short_circuits_vector_store(self):
        """缓存命中：在 store 构建链（load_user_vector_store 门面）之前短路返回（RP-08）。"""
        cached = [{"content": "缓存片段", "source": "c.md", "score": 0.5}]
        _, store, cache_svc, _, load_user_vector_store = self._patch_common(cached=cached)

        results = search_knowledge_base(_FakeUser(), "kb1", "查询")

        self.assertEqual(results, cached)
        store.similarity_search_with_score.assert_not_called()
        cache_svc.cache_search_result.assert_not_called()
        # RP-08：store 构建链（门面 load_user_vector_store）整体跳过
        load_user_vector_store.assert_not_called()

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

        _, _, cache_svc, reload_vector_store, _ = self._patch_common(store=bad_store, reload_store=good_store)

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
        # 自愈重建经门面返回的 reload 回调触发一次
        reload_vector_store.assert_called_once()
        # 自愈重试以索引元数据中的维度为参数
        self.assertEqual(reset_cache.call_args.kwargs.get("dimension"), 1024)
        cache_svc.cache_search_result.assert_called_once()

    def test_non_dimension_error_propagates(self):
        _, store, _, _, _ = self._patch_common()
        store.similarity_search_with_score.side_effect = ValueError("boom")

        with self.assertRaises(ValueError):
            search_knowledge_base(_FakeUser(), "kb1", "查询")

    def test_typed_dimension_mismatch_heals(self):
        """B-4附：类型化 DimensionMismatchError（isinstance 路径）同样触发自愈。"""
        from Django_xm.common.exceptions import DimensionMismatchError

        bad_store = mock.MagicMock(name="bad_store")
        bad_store.similarity_search_with_score.side_effect = DimensionMismatchError(
            "typed dimension mismatch"
        )
        good_store = mock.MagicMock(name="good_store")
        good_store.similarity_search_with_score.return_value = [
            (_make_doc("重试片段", "r.md"), 0.8),
        ]

        _, _, _, reload_vector_store, _ = self._patch_common(store=bad_store, reload_store=good_store)

        with (
            mock.patch.object(connections, "close_all"),
            mock.patch(f"{_PGV_RUNTIME}.reset_pgvector_cache") as reset_cache,
        ):
            results = search_knowledge_base(_FakeUser(), "kb1", "查询")

        self.assertEqual(
            results,
            [{"content": "重试片段", "source": "r.md", "score": 0.8}],
        )
        reload_vector_store.assert_called_once()
        reset_cache.assert_called_once()

    def test_non_pgvector_backend_does_not_self_heal(self):
        """B-4附 能力门控：无 _engine 的后端（faiss/chroma/milvus/inmemory）
        收到维度类错误原样上抛，不执行 pgvector 专属自愈。"""
        bad_store = mock.MagicMock(name="faiss_store", spec=["similarity_search_with_score"])
        bad_store.similarity_search_with_score.side_effect = RuntimeError(
            "different vector dimensions: 768 != 1024"
        )

        self._patch_common(store=bad_store)

        with self.assertRaises(RuntimeError):
            search_knowledge_base(_FakeUser(), "kb1", "查询")


if __name__ == "__main__":
    unittest.main()
