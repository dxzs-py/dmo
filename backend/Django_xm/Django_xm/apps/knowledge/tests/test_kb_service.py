"""kb_service.py 单元测试。

覆盖范围：
- list_knowledge_bases：过滤已删除索引、按 user 前缀过滤
- create_knowledge_base：新建 / 已存在 active（existing=True）/ 恢复软删 / 空名 / 索引已存在
- get_knowledge_base_detail：存在 / 不存在（FileNotFoundError）
- update_knowledge_base：存在 / 不存在（FileNotFoundError）
- delete_knowledge_base：存在 / 不存在（FileNotFoundError）/ 软删 DocumentIndex + Document
- list_documents：存在 / 不存在 / 文件列表
- search_knowledge_base：空查询（ValueError）/ 不存在（FileNotFoundError）
- rebuild_index_from_source_files：不存在 / 无文档（ValueError）

设计原则：
- IndexManager 通过 mock 注入，隔离文件系统与向量库
- DB 层使用 Django TestCase 管理 DocumentIndex / Document 事务
- 缓存层（invalidate_knowledge_cache / VectorSearchCacheService）通过 mock 隔离
- 跨模块副作用（clear_knowledge_base_selection）通过 mock 隔离
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from Django_xm.apps.knowledge.models import Document, DocumentIndex
from Django_xm.apps.knowledge.services import kb_service


def _mock_index_manager():
    """构造一个 IndexManager mock，提供 kb_service 用到的所有方法。"""
    m = MagicMock()
    m.index_exists.return_value = True
    m.list_indexes.return_value = []
    m.get_index_stats.return_value = {
        "num_documents": 5,
        "store_type": "pgvector",
        "embedding_model": "text-embedding-3-small",
    }
    m._load_metadata.return_value = {
        "name": "test_kb",
        "description": "测试描述",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
        "embedding_dimension": 1536,
    }
    m._save_metadata.return_value = None
    m.delete_index.return_value = None
    m.load_index.return_value = MagicMock()
    return m


# ============================================================================
# list_knowledge_bases 单测
# ============================================================================


class ListKnowledgeBasesTests(TestCase):
    """list_knowledge_bases 用户知识库列表。"""

    def test_returns_empty_when_no_indexes(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_user1", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.list_indexes.return_value = []
            result = kb_service.list_knowledge_bases(user)
        self.assertEqual(result, [])

    def test_filters_other_users_indexes(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_user2", password="pass123")
        # list_indexes 返回 user_99_other 的索引，应被过滤掉
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.list_indexes.return_value = [
                {
                    "name": f"user_{user.id}_my_kb",
                    "description": "我的",
                    "num_documents": 3,
                    "created_at": "",
                    "updated_at": "",
                },
                {
                    "name": "user_9999_other_kb",
                    "description": "别人的",
                    "num_documents": 1,
                    "created_at": "",
                    "updated_at": "",
                },
            ]
            result = kb_service.list_knowledge_bases(user)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "my_kb")
        self.assertEqual(result[0]["num_documents"], 3)

    def test_filters_soft_deleted_indexes(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_user3", password="pass123")
        # 创建一条软删记录
        DocumentIndex.all_objects.create(
            user=user,
            index_name="deleted_kb",
            description="",
            is_deleted=True,
            deleted_at=datetime.now(UTC),
        )
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.list_indexes.return_value = [
                {
                    "name": f"user_{user.id}_deleted_kb",
                    "description": "",
                    "num_documents": 0,
                    "created_at": "",
                    "updated_at": "",
                },
                {
                    "name": f"user_{user.id}_active_kb",
                    "description": "active",
                    "num_documents": 2,
                    "created_at": "",
                    "updated_at": "",
                },
            ]
            result = kb_service.list_knowledge_bases(user)
        names = [item["name"] for item in result]
        self.assertIn("active_kb", names)
        self.assertNotIn("deleted_kb", names)


# ============================================================================
# create_knowledge_base 单测
# ============================================================================


class CreateKnowledgeBaseTests(TestCase):
    """create_knowledge_base 知识库创建。"""

    def test_empty_name_raises_value_error(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_create1", password="pass123")
        with patch.object(kb_service, "IndexManager"), self.assertRaises(ValueError) as ctx:
            kb_service.create_knowledge_base(user, "")
        self.assertIn("不能为空", str(ctx.exception))

    def test_create_new_knowledge_base(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_create2", password="pass123")
        with (
            patch.object(kb_service, "IndexManager") as MockMgr,
            patch.object(kb_service, "invalidate_knowledge_cache"),
        ):
            MockMgr.return_value.index_exists.return_value = False
            result = kb_service.create_knowledge_base(user, "new_kb", "描述")
        self.assertEqual(result["name"], "new_kb")
        self.assertEqual(result["description"], "描述")
        self.assertFalse(result["existing"])
        self.assertEqual(result["document_count"], 0)
        # DB 记录已创建
        self.assertTrue(DocumentIndex.objects.filter(user=user, index_name="new_kb").exists())

    def test_index_already_exists_raises(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_create3", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.index_exists.return_value = True
            with self.assertRaises(ValueError) as ctx:
                kb_service.create_knowledge_base(user, "dup_kb")
        self.assertIn("已存在", str(ctx.exception))

    def test_existing_active_record_returns_existing_true(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_create4", password="pass123")
        # 先创建一条 active 记录
        DocumentIndex.objects.create(
            user=user,
            index_name="active_kb",
            description="旧描述",
            document_count=7,
        )
        with (
            patch.object(kb_service, "IndexManager") as MockMgr,
            patch.object(kb_service, "invalidate_knowledge_cache"),
        ):
            MockMgr.return_value.index_exists.return_value = False
            result = kb_service.create_knowledge_base(user, "active_kb", "新描述")
        self.assertTrue(result["existing"])
        # 已存在记录不更新描述（返回 DB 中的旧描述）
        self.assertEqual(result["description"], "旧描述")
        self.assertEqual(result["document_count"], 7)

    def test_restore_soft_deleted_record(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_create5", password="pass123")
        # 创建一条软删记录
        DocumentIndex.all_objects.create(
            user=user,
            index_name="restored_kb",
            description="旧",
            is_deleted=True,
            deleted_at=datetime.now(UTC),
        )
        with (
            patch.object(kb_service, "IndexManager") as MockMgr,
            patch.object(kb_service, "invalidate_knowledge_cache"),
        ):
            MockMgr.return_value.index_exists.return_value = False
            result = kb_service.create_knowledge_base(user, "restored_kb", "恢复后描述")
        self.assertFalse(result["existing"])
        # 记录已恢复
        idx = DocumentIndex.objects.get(user=user, index_name="restored_kb")
        self.assertFalse(idx.is_deleted)
        self.assertIsNone(idx.deleted_at)
        self.assertEqual(idx.description, "恢复后描述")


# ============================================================================
# get_knowledge_base_detail 单测
# ============================================================================


class GetKnowledgeBaseDetailTests(TestCase):
    """get_knowledge_base_detail 知识库详情。"""

    def test_nonexistent_raises_filenotfounderror(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_detail1", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.index_exists.return_value = False
            with self.assertRaises(FileNotFoundError) as ctx:
                kb_service.get_knowledge_base_detail(user, "missing_kb")
        self.assertIn("missing_kb", str(ctx.exception))

    def test_returns_metadata_and_stats(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_detail2", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.index_exists.return_value = True
            MockMgr.return_value.get_index_stats.return_value = {
                "num_documents": 42,
                "store_type": "pgvector",
                "embedding_model": "text-embedding-3-small",
            }
            MockMgr.return_value._load_metadata.return_value = {
                "description": "详情描述",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-02T00:00:00+00:00",
            }
            result = kb_service.get_knowledge_base_detail(user, "my_kb")
        self.assertEqual(result["id"], "my_kb")
        self.assertEqual(result["name"], "my_kb")
        self.assertEqual(result["description"], "详情描述")
        self.assertEqual(result["chunk_count"], 42)
        self.assertEqual(result["store_type"], "pgvector")
        self.assertEqual(result["embedding_model"], "text-embedding-3-small")


# ============================================================================
# update_knowledge_base 单测
# ============================================================================


class UpdateKnowledgeBaseTests(TestCase):
    """update_knowledge_base 更新描述。"""

    def test_nonexistent_raises_filenotfounderror(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_upd1", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.index_exists.return_value = False
            with self.assertRaises(FileNotFoundError):
                kb_service.update_knowledge_base(user, "missing", "新描述")

    def test_updates_metadata_and_db_record(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_upd2", password="pass123")
        DocumentIndex.objects.create(
            user=user,
            index_name="upd_kb",
            description="旧描述",
        )
        with (
            patch.object(kb_service, "IndexManager") as MockMgr,
            patch.object(kb_service, "invalidate_knowledge_cache"),
        ):
            MockMgr.return_value.index_exists.return_value = True
            MockMgr.return_value._load_metadata.return_value = {"description": "旧描述"}
            result = kb_service.update_knowledge_base(user, "upd_kb", "新描述")
        self.assertEqual(result["description"], "新描述")
        MockMgr.return_value._save_metadata.assert_called_once()
        # DB 记录描述已更新
        idx = DocumentIndex.objects.get(user=user, index_name="upd_kb")
        self.assertEqual(idx.description, "新描述")


# ============================================================================
# delete_knowledge_base 单测
# ============================================================================


class DeleteKnowledgeBaseTests(TestCase):
    """delete_knowledge_base 删除知识库。"""

    def test_nonexistent_raises_filenotfounderror(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_del1", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.index_exists.return_value = False
            with self.assertRaises(FileNotFoundError):
                kb_service.delete_knowledge_base(user, "missing")

    def test_deletes_index_db_record_and_cache(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_del2", password="pass123")
        idx = DocumentIndex.objects.create(
            user=user,
            index_name="del_kb",
            description="",
            document_count=2,
        )
        Document.objects.create(
            index=idx,
            filename="a.txt",
            file_path=str(Path(tempfile.gettempdir()) / "a.txt"),
            file_type="txt",
            file_size=10,
            chunk_count=1,
        )
        with (
            patch.object(kb_service, "IndexManager") as MockMgr,
            patch.object(kb_service, "invalidate_knowledge_cache"),
            patch("Django_xm.apps.chat.services.cross_app.clear_knowledge_base_selection") as mock_clear,
        ):
            MockMgr.return_value.index_exists.return_value = True
            kb_service.delete_knowledge_base(user, "del_kb")
        MockMgr.return_value.delete_index.assert_called_once()
        # DB 记录已软删
        idx.refresh_from_db()
        self.assertTrue(idx.is_deleted)
        # Document 已软删
        doc = Document.all_objects.get(index=idx, filename="a.txt")
        self.assertTrue(doc.is_deleted)
        # 跨模块清理已调用
        mock_clear.assert_called_once_with(user_id=user.id, kb_name="del_kb")


# ============================================================================
# list_documents 单测
# ============================================================================


class ListDocumentsTests(TestCase):
    """list_documents 文档列表。"""

    def test_nonexistent_raises_filenotfounderror(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_ldoc1", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.index_exists.return_value = False
            with self.assertRaises(FileNotFoundError):
                kb_service.list_documents(user, "missing")

    def test_returns_empty_when_upload_dir_missing(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_ldoc2", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr, patch.object(kb_service, "Path") as MockPath:
            MockMgr.return_value.index_exists.return_value = True
            MockPath.return_value.exists.return_value = False
            result = kb_service.list_documents(user, "empty_kb")
        self.assertEqual(result, [])

    def test_returns_files_from_upload_dir(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_ldoc3", password="pass123")
        # 模拟文件
        fake_file = MagicMock()
        fake_file.is_file.return_value = True
        fake_file.name = "doc.pdf"
        fake_file.stat.return_value = MagicMock(st_size=1024, st_ctime=1700000000)
        fake_dir = MagicMock()
        fake_dir.exists.return_value = True
        fake_dir.iterdir.return_value = [fake_file]
        # Path(...) / user_index_name 需返回同一 fake_dir，否则 iterdir 在新 mock 上失效
        fake_dir.__truediv__.return_value = fake_dir
        with (
            patch.object(kb_service, "IndexManager") as MockMgr,
            patch.object(kb_service, "Path", return_value=fake_dir),
        ):
            MockMgr.return_value.index_exists.return_value = True
            result = kb_service.list_documents(user, "my_kb")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "doc.pdf")
        self.assertEqual(result[0]["size"], 1024)


# ============================================================================
# search_knowledge_base 单测
# ============================================================================


class SearchKnowledgeBaseTests(TestCase):
    """search_knowledge_base 知识库搜索。"""

    def test_empty_query_raises_value_error(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_search1", password="pass123")
        with patch.object(kb_service, "IndexManager"), self.assertRaises(ValueError) as ctx:
            kb_service.search_knowledge_base(user, "my_kb", "")
        self.assertIn("不能为空", str(ctx.exception))

    def test_nonexistent_kb_raises_filenotfounderror(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_search2", password="pass123")
        with patch.object(kb_service, "IndexManager") as MockMgr:
            MockMgr.return_value.index_exists.return_value = False
            with self.assertRaises(FileNotFoundError):
                kb_service.search_knowledge_base(user, "missing", "查询")

    def test_returns_cached_results_when_available(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_search3", password="pass123")
        cached = [{"content": "缓存结果", "source": "s", "score": 0.9}]
        with (
            patch.object(kb_service, "IndexManager") as MockMgr,
            patch.object(kb_service, "get_embeddings"),
            patch.object(kb_service, "VectorSearchCacheService") as MockCache,
            patch("Django_xm.apps.knowledge.vector_store.search_vector_store") as mock_search,
        ):
            MockMgr.return_value.index_exists.return_value = True
            MockMgr.return_value._load_metadata.return_value = {}
            MockCache.get_cached_search.return_value = cached
            result = kb_service.search_knowledge_base(user, "my_kb", "查询", top_k=5)
        self.assertEqual(result, cached)
        # 缓存命中时不应执行实际向量搜索
        mock_search.assert_not_called()


# ============================================================================
# rebuild_index_from_source_files 单测
# ============================================================================


class RebuildIndexFromSourceFilesTests(TestCase):
    """rebuild_index_from_source_files 从源文件重建索引。"""

    def test_nonexistent_kb_raises_filenotfounderror(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_rb1", password="pass123")
        with patch.object(kb_service, "IndexManager"), self.assertRaises(FileNotFoundError) as ctx:
            kb_service.rebuild_index_from_source_files(user, "user_1_missing")
        self.assertIn("missing", str(ctx.exception))

    def test_no_documents_raises_value_error(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_rb2", password="pass123")
        DocumentIndex.objects.create(
            user=user,
            index_name="empty_rb",
            description="",
        )
        with patch.object(kb_service, "IndexManager"), self.assertRaises(ValueError) as ctx:
            kb_service.rebuild_index_from_source_files(user, f"user_{user.id}_empty_rb")
        self.assertIn("无法从原始文件加载", str(ctx.exception))

    def test_rebuilds_from_existing_documents(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="kb_rb3", password="pass123")
        idx = DocumentIndex.objects.create(
            user=user,
            index_name="rebuild_kb",
            description="",
        )
        # 创建一个临时文件作为源文件
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("重建内容")
            tmp_path = f.name
        try:
            Document.objects.create(
                index=idx,
                filename="src.txt",
                file_path=tmp_path,
                file_type="txt",
                file_size=100,
                chunk_count=1,
            )
            mock_embeddings = MagicMock()
            with (
                patch.object(kb_service, "IndexManager") as MockMgr,
                patch.object(kb_service, "load_document", return_value=[MagicMock()]),
                patch.object(kb_service, "split_documents", return_value=[MagicMock()]),
                patch.object(kb_service, "invalidate_knowledge_cache"),
            ):
                MockMgr.return_value.create_index.return_value = None
                # 不应抛异常
                kb_service.rebuild_index_from_source_files(
                    user,
                    f"user_{user.id}_rebuild_kb",
                    embeddings=mock_embeddings,
                )
            MockMgr.return_value.create_index.assert_called_once()
            # 验证 overwrite=True
            _, kwargs = MockMgr.return_value.create_index.call_args
            self.assertTrue(kwargs.get("overwrite"))
            self.assertEqual(kwargs.get("store_type"), "pgvector")
        finally:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
