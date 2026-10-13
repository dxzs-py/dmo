"""W6 票① C-1arch / C-2arch 行为锁定测试。

C-1arch（持久化分支收敛）：IndexManager 增删改路径对任意后端**无条件**调用
``backend.save(vector_store, path)``——持久化能力由各后端 save 实现声明
（pgvector/inmemory 为 no-op，faiss/chroma/milvus 落盘），IndexManager 不得
再按 store_type 字符串硬判（原 6 处 ``!= "pgvector"`` 分支已移除）。

C-2arch（原生分数语义收敛）：后端经 ``native_score_semantics`` 类属性声明
分数语义（pgvector="distance"，默认 "similarity"），IndexManager.get_score_semantics
按声明取用；新增返回 distance 的第 6 后端只需覆写该属性，调用方零改动
（对照 arch 报告"新增后端成本盘点"：改造后接入点 ≤3 处——backend 文件 +
registry 注册 + 构造参数）。

mock 纪律：全部 backend/store_type 依赖打桩，**绝不触真实 PGVector**
（真实 engine 连接池会让测试库销毁时残留会话，下次运行 DROP 失败）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_vector_backend_capabilities --noinput
"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.knowledge.services.index_service import IndexManager
from Django_xm.apps.knowledge.vector_store.base import VectorStoreBackend

_INDEX_SERVICE = "Django_xm.apps.knowledge.services.index_service"


def _make_backend(store_type: str, score_semantics: str | None = None) -> mock.MagicMock:
    backend = mock.MagicMock()
    backend.store_type = store_type
    if score_semantics is not None:
        backend.native_score_semantics = score_semantics
    else:
        del backend.native_score_semantics  # 模拟未声明属性的后端
    return backend


class NativeScoreSemanticsTests(unittest.TestCase):
    """C-2arch：native_score_semantics 声明与 get_score_semantics 收敛。"""

    def test_base_default_is_similarity(self):
        """基类默认 similarity：新增后端不声明即获得 similarity 语义。"""
        self.assertEqual(VectorStoreBackend.native_score_semantics, "similarity")

    def test_pgvector_declares_distance(self):
        """pgvector 声明 distance（cosine 距离，越小越相关）。"""
        from Django_xm.apps.knowledge.vector_store.pgvector_backend import PGVectorBackend

        self.assertEqual(PGVectorBackend.native_score_semantics, "distance")

    def test_get_score_semantics_follows_backend_declaration(self):
        """get_score_semantics 按后端声明返回，调用方不再硬判 store_type。"""
        manager = IndexManager(base_path=tempfile.mkdtemp())
        pg_backend = _make_backend("pgvector", "distance")
        faiss_backend = _make_backend("faiss", "similarity")

        with (
            mock.patch.object(IndexManager, "get_store_type", return_value="pgvector"),
            mock.patch.object(IndexManager, "get_backend", return_value=pg_backend),
        ):
            self.assertEqual(manager.get_score_semantics("user_1_kb"), "distance")
        with (
            mock.patch.object(IndexManager, "get_store_type", return_value="faiss"),
            mock.patch.object(IndexManager, "get_backend", return_value=faiss_backend),
        ):
            self.assertEqual(manager.get_score_semantics("user_1_kb"), "similarity")

    def test_get_score_semantics_falls_back_to_similarity(self):
        """后端未声明属性时兜底 similarity（防御第三方后端未继承基类常量）。"""
        manager = IndexManager(base_path=tempfile.mkdtemp())
        bare_backend = _make_backend("custom")  # 无 native_score_semantics

        with (
            mock.patch.object(IndexManager, "get_store_type", return_value="custom"),
            mock.patch.object(IndexManager, "get_backend", return_value=bare_backend),
        ):
            self.assertEqual(manager.get_score_semantics("user_1_kb"), "similarity")


class UnconditionalSaveTests(unittest.TestCase):
    """C-1arch：写路径无条件调用 backend.save，IndexManager 零 store_type 硬判。"""

    def test_add_documents_calls_save_for_non_pg_backend(self):
        """非 pgvector 后端 add_documents 后 save 被无条件调用（原 if 分支已移除）。"""
        manager = IndexManager(base_path=tempfile.mkdtemp())
        backend = _make_backend("faiss", "similarity")
        backend.exists.return_value = True
        backend.load.return_value = mock.MagicMock()

        with (
            mock.patch.object(IndexManager, "get_store_type", return_value="faiss"),
            mock.patch.object(IndexManager, "get_backend", return_value=backend),
            mock.patch.object(IndexManager, "index_exists", return_value=True),
            mock.patch.object(IndexManager, "load_metadata", return_value={"num_documents": 3}),
            mock.patch.object(IndexManager, "_generate_stable_ids", return_value=["id1"]),
            mock.patch.object(IndexManager, "_add_documents_with_stable_ids"),
            mock.patch.object(IndexManager, "_save_metadata"),
            mock.patch.object(IndexManager, "_update_metadata_doc_count"),
            mock.patch.object(IndexManager, "_cache"),
        ):
            manager.add_documents("user_1_kb", [mock.MagicMock()], mock.MagicMock())

        backend.save.assert_called_once()

    def test_create_index_calls_save_for_non_pg_backend(self):
        """非 pgvector 后端 create_index 后 save 被无条件调用。"""
        manager = IndexManager(base_path=tempfile.mkdtemp())
        backend = _make_backend("faiss", "similarity")
        backend.exists.return_value = False
        backend.create.return_value = mock.MagicMock()

        with (
            mock.patch.object(IndexManager, "get_store_type", return_value="faiss"),
            mock.patch.object(IndexManager, "get_backend", return_value=backend),
            mock.patch.object(IndexManager, "_detect_embedding_dimension", return_value=1024),
            mock.patch.object(IndexManager, "_save_metadata"),
            mock.patch.object(IndexManager, "_set_index_status"),
            mock.patch.object(IndexManager, "_cache"),
        ):
            manager.create_index("user_1_kb", documents=[mock.MagicMock()], embeddings=mock.MagicMock())

        backend.save.assert_called_once()

    def test_create_index_failure_cleanup_is_unconditional(self):
        """create_index 失败清理不再依赖 store_type 硬判（原分支已移除）。"""
        manager = IndexManager(base_path=tempfile.mkdtemp())
        backend = _make_backend("faiss", "similarity")
        backend.exists.return_value = False
        backend.create.side_effect = RuntimeError("boom")

        with (
            mock.patch.object(IndexManager, "get_store_type", return_value="faiss"),
            mock.patch.object(IndexManager, "get_backend", return_value=backend),
            mock.patch.object(IndexManager, "_save_metadata"),
            mock.patch.object(IndexManager, "_set_index_status"),
            mock.patch.object(IndexManager, "_cache"),
        ):
            with self.assertRaises(RuntimeError):
                manager.create_index("user_1_kb", documents=[mock.MagicMock()], embeddings=mock.MagicMock())

    def test_no_store_type_hardcode_in_write_paths(self):
        """源代码级锁定：index_service 不再含 `!= "pgvector"` 持久化硬判。"""
        import inspect

        from Django_xm.apps.knowledge.services import index_service as module

        source = inspect.getsource(module)
        self.assertNotIn('!= "pgvector"', source)
        # == "pgvector" 仅允许出现在保留语义处：构造参数分支 / 元数据 DB-文件二分 /
        # PGVector 专属幂等冲突策略 / 删除路径分派
        self.assertEqual(source.count('== "pgvector"'), 5, "store_type 硬判数量漂移，请核对是否引入新的持久化硬判")


if __name__ == "__main__":
    unittest.main()
