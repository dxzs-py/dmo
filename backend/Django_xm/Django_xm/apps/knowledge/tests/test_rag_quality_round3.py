"""fix-review-round3 Task 3（RAG 质量与诚实性）行为锁定测试。

覆盖：
- 3.2 KB 主链路相似度阈值（kb_service 侧在 test_retrieval_entry_convergence
  / test_retrieval_with_scores 锁定，此处不重复）
- 3.3 FAISS/InMemory 假删除显式化：能力矩阵 supports_remove/supports_stats、
  不支持删除的后端抛 NotImplementedError（禁止 False/0 假成功）、
  InMemory 真实删除与真实计数、IndexManager 删除链 load→delete→save 回退
- 3.4 pgvector 后端级 search 分数归一（distance → similarity）
- 3.5 multi_kb 越权守卫：构建期 DB 归属过滤 + 运行期前缀硬守卫
- 3.6 SearchAll 按库各自维度构造 embeddings（两库不同维度均成功）
- 3.7 pgvector read_all_documents fetchmany 分批遍历
- 3.8 rerank 降级可见性（error 级 degradation 留痕）

运行（backend/Django_xm 目录，conda env langchain_xm，测试串行）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_quality_round3 --noinput
（除 3.6 需测试库的 401 场景外均为纯单元测试；本文件全部 mock 不触真实 PG）
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.documents import Document

from Django_xm.apps.knowledge.vector_store.base import VectorStoreBackend
from Django_xm.apps.knowledge.vector_store.faiss_backend import FAISSBackend
from Django_xm.apps.knowledge.vector_store.inmemory_backend import InMemoryBackend
from Django_xm.apps.knowledge.vector_store.pgvector_backend import PGVectorBackend

_INDEX_SERVICE = "Django_xm.apps.knowledge.services.index_service"
_PGV = "Django_xm.apps.knowledge.vector_store.pgvector_backend"
_VIEWS_RAG = "Django_xm.apps.knowledge.views_rag"


class _FakeEmbeddings:
    """确定性 Embeddings 桩（InMemoryVectorStore.from_documents 需要）"""

    def embed_documents(self, texts):
        return [[float(len(t)) % 7 / 7] * 4 for t in texts]

    def embed_query(self, text):
        return [0.1] * 4


def _doc(content: str, source: str, **extra):
    return Document(page_content=content, metadata={"source": source, **extra})


# ── 3.3 能力矩阵与假删除显式化 ────────────────────────────────────────────────


class BackendCapabilityMatrixTests(unittest.TestCase):
    """supports_remove / supports_stats 声明与 NotImplementedError 契约。"""

    def test_capability_declarations(self):
        self.assertTrue(VectorStoreBackend.supports_remove)
        self.assertTrue(VectorStoreBackend.supports_stats)
        self.assertFalse(FAISSBackend.supports_remove, "FAISS 无法文档级删除，必须显式声明")
        self.assertTrue(InMemoryBackend.supports_remove)

    def test_faiss_remove_raises_not_implemented(self):
        """FAISS 直接调用删除接口：显式异常而非返回 False/0 假成功。"""
        backend = FAISSBackend(base_path=tempfile.mkdtemp())

        with self.assertRaises(NotImplementedError):
            backend.remove_documents("user_1_kb", ["id1"])
        with self.assertRaises(NotImplementedError):
            backend.remove_documents_by_metadata("user_1_kb", "file_name", "a.txt")

    def test_inmemory_remove_documents_is_real(self):
        """InMemory 按 ID 删除为真实实现（旧实现恒返 False）。"""
        backend = InMemoryBackend()
        store = backend.create(
            [_doc("甲内容", "a.txt", file_name="a.txt"), _doc("乙内容", "b.txt", file_name="b.txt")],
            _FakeEmbeddings(),
            "user_1_kb",
        )
        ids = backend.add_documents(store, [_doc("丙内容", "c.txt", file_name="c.txt")])

        self.assertTrue(backend.remove_documents("user_1_kb", ids))
        stats = backend.get_stats("user_1_kb")
        self.assertEqual(stats["num_documents"], 2, "删除后真实计数应减少")

    def test_inmemory_remove_by_metadata_is_real(self):
        """InMemory 按元数据删除为真实实现（旧实现恒返 0）。"""
        backend = InMemoryBackend()
        _ = backend.create(
            [_doc("甲内容", "a.txt", file_name="a.txt"), _doc("乙内容", "b.txt", file_name="b.txt")],
            _FakeEmbeddings(),
            "user_1_kb",
        )
        removed = backend.remove_documents_by_metadata("user_1_kb", "file_name", "a.txt")

        self.assertEqual(removed, 1)
        self.assertEqual(backend.get_stats("user_1_kb")["num_documents"], 1)

    def test_inmemory_stats_real_count(self):
        """InMemory get_stats 返回真实文档数（旧实现恒 0）。"""
        backend = InMemoryBackend()
        store = backend.create(
            [_doc("甲", "a.txt"), _doc("乙", "b.txt"), _doc("丙", "c.txt")],
            _FakeEmbeddings(),
            "user_1_kb",
        )
        self.assertEqual(backend.get_stats("user_1_kb")["num_documents"], len(store.store))
        self.assertFalse(backend.get_stats("missing")["exists"])


class IndexManagerRemoveFallbackTests(unittest.TestCase):
    """3.3：supports_remove=False 的后端走 load → delete → save 通用回退。"""

    def test_unsupported_backend_falls_back_to_load_delete_save(self):
        backend = mock.MagicMock(name="backend")
        backend.supports_remove = False
        vector_store = mock.MagicMock(name="vector_store")
        backend.load.return_value = vector_store

        with (
            mock.patch(f"{_INDEX_SERVICE}.IndexManager.get_store_type", return_value="faiss"),
            mock.patch(f"{_INDEX_SERVICE}.IndexManager.get_backend", return_value=backend),
            mock.patch(f"{_INDEX_SERVICE}.IndexManager.index_exists", return_value=True),
            mock.patch(f"{_INDEX_SERVICE}.IndexManager.load_index", return_value=vector_store),
            mock.patch(f"{_INDEX_SERVICE}.IndexManager._get_index_path", return_value="/tmp/x"),
        ):
            from Django_xm.apps.knowledge.services.index_service import IndexManager

            real_manager = IndexManager(base_path=tempfile.mkdtemp())
            removed = real_manager.remove_documents("user_1_kb", mock.MagicMock(), ["id1", "id2"])

        self.assertEqual(removed, 2)
        vector_store.delete.assert_called_once_with(["id1", "id2"])
        backend.save.assert_called_once()
        backend.remove_documents.assert_not_called()


# ── 3.4 pgvector 后端级分数归一 ───────────────────────────────────────────────


class PGVectorSearchScoreNormalizationTests(unittest.TestCase):
    def test_search_normalizes_distance_to_similarity(self):
        """后端级 search 输出 similarity = 1 - distance（None 分数保留）。"""
        backend = PGVectorBackend()
        store = mock.MagicMock(name="pgvector_store")
        store.similarity_search_with_score.return_value = [
            (_doc("最相关", "a.md"), 0.1),
            (_doc("中等", "b.md"), 0.4),
            (_doc("无分数", "c.md"), None),
        ]

        results = backend.search(store, "查询", k=3)

        self.assertEqual(results[0][1], 0.9)
        self.assertEqual(results[1][1], 0.6)
        self.assertIsNone(results[2][1])
        store.similarity_search_with_score.assert_called_once_with(query="查询", k=3)


# ── 3.5 multi_kb 越权守卫 ─────────────────────────────────────────────────────


class MultiKbOwnershipTests(unittest.TestCase):
    def _patch_metadata(self, owned_names):
        metadata_cls = mock.MagicMock(name="IndexMetadata")
        metadata_cls.objects.filter.return_value.values_list.return_value = owned_names
        return mock.patch("Django_xm.apps.knowledge.models.IndexMetadata", metadata_cls)

    def test_build_tool_drops_cross_tenant_kb_ids(self):
        """非本人 kb_id 构建期剔除并留痕；工具只含本人库。"""
        from Django_xm.apps.knowledge.services import multi_kb_retriever

        with (
            self._patch_metadata(["user_7_mine"]),
            mock.patch(
                "Django_xm.apps.knowledge.views_utils.build_user_index_name",
                side_effect=lambda uid, kb: f"user_{uid}_{kb}",
            ),
            self.assertLogs("Django_xm.apps.knowledge.services.multi_kb_retriever", level="WARNING") as logs,
        ):
            tool = multi_kb_retriever.build_retriever_tool_for_research(
                knowledge_base_ids=["mine", "other-user-kb"], user_id=7
            )

        self.assertIsNotNone(tool)
        self.assertTrue(any("other-user-kb" in message for message in logs.output))

    def test_build_tool_returns_none_when_all_denied(self):
        from Django_xm.apps.knowledge.services import multi_kb_retriever

        with (
            self._patch_metadata([]),
            mock.patch(
                "Django_xm.apps.knowledge.views_utils.build_user_index_name",
                side_effect=lambda uid, kb: f"user_{uid}_{kb}",
            ),
        ):
            tool = multi_kb_retriever.build_retriever_tool_for_research(
                knowledge_base_ids=["ghost1", "ghost2"], user_id=7
            )

        self.assertIsNone(tool)

    def test_search_multi_kb_prefix_guard_rejects_foreign_names(self):
        """运行期前缀硬守卫：非 user_{uid}_ 前缀的库被剔除，不触碰向量库。"""
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        manager_cls = mock.MagicMock(name="IndexManager")
        with (
            mock.patch(f"{_INDEX_SERVICE}.IndexManager", manager_cls),
            mock.patch(
                "Django_xm.apps.knowledge.services.embedding_service.get_embeddings"
            ),
        ):
            output = UnifiedRagPipeline(scenario="knowledge_base").search_multi_kb(
                kb_names=["user_999_victim"], query="查询", user_id=1
            )

        self.assertEqual(output, "知识库中暂无文档，请先上传文档。")
        manager_cls.return_value.load_index.assert_not_called()


# ── 3.6 SearchAll 按库维度分组 ────────────────────────────────────────────────


class SearchAllDimensionTests(unittest.TestCase):
    """两库不同维度：各自构造 embeddings，两库均检索成功。"""

    def test_two_kbs_different_dimensions_both_succeed(self):
        from Django_xm.apps.knowledge.views_rag import SearchAllView

        stores = {
            "user_1_kb1": mock.MagicMock(name="store_kb1"),
            "user_1_kb2": mock.MagicMock(name="store_kb2"),
        }
        for name, (content, score) in {
            "user_1_kb1": ("kb1内容", 0.9),
            "user_1_kb2": ("kb2内容", 0.8),
        }.items():
            stores[name].similarity_search_with_score_by_vector.return_value = [
                (_doc(content, f"{name}.md"), score)
            ]

        embeddings_by_dim = {}
        for dim in (1024, 768):
            emb = mock.MagicMock(name=f"embeddings_{dim}")
            emb.embed_query.return_value = [dim % 7] * 8
            embeddings_by_dim[dim] = emb

        def _fake_get_embeddings(required_dimension=None):
            return embeddings_by_dim[required_dimension]

        manager = mock.MagicMock(name="IndexManager")
        manager.load_index.side_effect = lambda name, embeddings: stores[name]
        manager.get_score_semantics.return_value = "similarity"

        prepared = SimpleNamespace(
            query="查询",
            k=4,
            score_threshold=None,
            targets=["user_1_kb1", "user_1_kb2"],
            records={"user_1_kb1": (3, 1024), "user_1_kb2": (3, 768)},
        )

        with (
            mock.patch(f"{_VIEWS_RAG}.get_embeddings", side_effect=_fake_get_embeddings),
            mock.patch(f"{_VIEWS_RAG}.IndexManager", return_value=manager),
            mock.patch(f"{_VIEWS_RAG}.ai_settings") as ai_settings,
        ):
            ai_settings.rag_rrf_constant = 60
            data, _message = SearchAllView._run_search(prepared)

        self.assertEqual(data["searched_indexes"], 2)
        self.assertEqual(data["failed_indexes"], 0)
        # 各库使用自己维度的 query 向量（by-vector 路径），不共用对方向量
        stores["user_1_kb1"].similarity_search_with_score_by_vector.assert_called_once()
        stores["user_1_kb2"].similarity_search_with_score_by_vector.assert_called_once()
        self.assertEqual(
            stores["user_1_kb1"].similarity_search_with_score_by_vector.call_args.args[0],
            embeddings_by_dim[1024].embed_query.return_value,
        )
        self.assertEqual(
            stores["user_1_kb2"].similarity_search_with_score_by_vector.call_args.args[0],
            embeddings_by_dim[768].embed_query.return_value,
        )


# ── 3.7 pgvector read_all_documents 分批加载 ─────────────────────────────────


class ReadAllDocumentsBatchingTests(unittest.TestCase):
    def test_fetchmany_batched_loading(self):
        """fetchmany 分批遍历：>批大小 时不调用 fetchall。"""
        backend = PGVectorBackend()

        cursor = mock.MagicMock(name="cursor")
        batch1 = [(f"内容{i}", {"source": f"f{i}.md"}) for i in range(500)]
        batch2 = [("尾批内容", {"source": "tail.md"})]
        cursor.fetchmany.side_effect = [batch1, batch2, []]

        conn = mock.MagicMock(name="connection")
        conn.cursor.return_value.__enter__.return_value = cursor

        with (
            mock.patch(f"{_PGV}.get_embedding_table_name", return_value="langchain_pg_embedding"),
            mock.patch(f"{_PGV}.get_collection_table_name", return_value="langchain_pg_collection"),
            mock.patch(f"{_PGV}.check_table_exists", return_value=True),
            mock.patch(f"{_PGV}.connections") as connections_mod,
        ):
            connections_mod.__getitem__.return_value = conn
            docs = backend.read_all_documents("user_1_kb")

        self.assertEqual(len(docs), 501)
        # 3.7：分批取行（两次数据批 + 一次终止探测），禁止一次性 fetchall
        self.assertGreaterEqual(cursor.fetchmany.call_count, 3)
        cursor.fetchall.assert_not_called()
        self.assertEqual(cursor.fetchmany.call_args_list[0].args[0], PGVectorBackend.READ_ALL_BATCH_SIZE)


# ── 3.8 rerank 降级可见性 ─────────────────────────────────────────────────────


class RerankDegradationVisibilityTests(unittest.TestCase):
    def test_reranker_unavailable_emits_error_degradation(self):
        from Django_xm.apps.knowledge.services import retrieval_service

        with (
            mock.patch.object(retrieval_service, "FLASHRANK_AVAILABLE", False),
            self.assertLogs(retrieval_service.logger, level="ERROR") as logs,
        ):
            self.assertIsNone(retrieval_service.create_reranker())

        self.assertTrue(any("degradation=rerank_unavailable" in line for line in logs.output))

    def test_reranker_construction_failure_emits_error_degradation(self):
        from Django_xm.apps.knowledge.services import retrieval_service

        factory = mock.MagicMock(name="get_flashrank_reranker", side_effect=RuntimeError("onnx down"))
        with (
            mock.patch.object(retrieval_service, "FLASHRANK_AVAILABLE", True),
            mock.patch.object(retrieval_service, "get_flashrank_reranker", factory),
            self.assertLogs(retrieval_service.logger, level="ERROR") as logs,
        ):
            self.assertIsNone(retrieval_service.create_reranker("m", 5))

        self.assertTrue(any("degradation=rerank_unavailable" in line for line in logs.output))

    def test_compression_retriever_unavailable_emits_error_degradation(self):
        from Django_xm.apps.knowledge.services import retrieval_service

        base_retriever = mock.MagicMock(name="base_retriever")
        with (
            mock.patch.object(retrieval_service, "ADVANCED_RETRIEVERS_AVAILABLE", False),
            self.assertLogs(retrieval_service.logger, level="ERROR") as logs,
        ):
            result = retrieval_service.create_reranking_retriever(base_retriever, reranker=mock.MagicMock())

        self.assertIs(result, base_retriever)
        self.assertTrue(any("degradation=rerank_unavailable" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
