"""知识库生命周期测试（dj-13：IndexMetadata 唯一实体）。

服务层直测（mock IndexManager / 缓存失效 / 跨 app 选库清理），覆盖：
- 创建：IndexMetadata 显式落 user 归属 + 向量索引创建 + 缓存失效
- 列表：软删墓碑被 objects 过滤；document_count 派生（滤软删 Document）vs chunk_count 镜像
- 删除：IndexMetadata/Document 软删墓碑 + 向量硬删 + 会话选库清理
- 同名重建：命中墓碑 → 恢复（状态归零）而非 duplicate

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_kb_lifecycle --settings=Django_xm.settings.test
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from Django_xm.apps.knowledge.models import Document, IndexMetadata
from Django_xm.apps.knowledge.services.kb_service import (
    create_knowledge_base,
    delete_knowledge_base,
    list_knowledge_bases,
)

_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"
_CROSS_APP = "Django_xm.apps.chat.services.cross_app"


class KnowledgeBaseLifecycleTests(TestCase):
    """IndexMetadata 唯一实体的知识库生命周期。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="kblifecycle", password="pw123456")

        manager_patcher = mock.patch(f"{_KB_SERVICE}.IndexManager")
        self.manager_cls = manager_patcher.start()
        self.manager_cls.return_value.index_exists.return_value = False
        self.addCleanup(manager_patcher.stop)

        cache_patcher = mock.patch(f"{_KB_SERVICE}.invalidate_knowledge_cache")
        self.invalidate_cache = cache_patcher.start()
        self.addCleanup(cache_patcher.stop)

    def _create_document(self, index, filename, *, soft_deleted=False):
        return Document.objects.create(
            index=index,
            filename=filename,
            file_path=f"/uploads/{filename}",
            file_size=100,
            chunk_count=3,
            is_deleted=soft_deleted,
        )

    def test_create_persists_user_ownership(self):
        """全新创建 → IndexMetadata 显式落 user 归属（根因修复回归）。"""
        result = create_knowledge_base(self.user, "kb-new", "描述")

        self.assertFalse(result["existing"])
        self.assertEqual(result["document_count"], 0)
        self.assertEqual(result["chunk_count"], 0)

        meta = IndexMetadata.all_objects.get(name=f"user_{self.user.id}_kb-new")
        self.assertEqual(meta.user_id, self.user.id)
        self.assertEqual(meta.status, IndexMetadata.IndexStatus.EMPTY)

        self.manager_cls.return_value.create_empty_index.assert_called_once_with(
            name=f"user_{self.user.id}_kb-new", description="描述"
        )
        self.invalidate_cache.assert_called_once_with(user_id=self.user.id)

    def test_list_filters_tombstones_and_derives_counts(self):
        """列表：软删墓碑不可见；document_count 派生 vs chunk_count 镜像。"""
        active = IndexMetadata.objects.create(
            user=self.user, name=f"user_{self.user.id}_kb-a", num_documents=7
        )
        IndexMetadata.objects.create(
            user=self.user, name=f"user_{self.user.id}_kb-tomb", num_documents=9, is_deleted=True
        )
        self._create_document(active, "a.pdf")
        self._create_document(active, "b.pdf", soft_deleted=True)

        kbs = list_knowledge_bases(self.user)

        self.assertEqual([kb["id"] for kb in kbs], ["kb-a"])  # 墓碑被 objects 过滤
        kb_a = kbs[0]
        self.assertEqual(kb_a["document_count"], 1)  # 派生文件数：滤软删 Document
        self.assertEqual(kb_a["chunk_count"], 7)  # 向量块数镜像
        self.assertNotIn("num_documents", kb_a)  # 旧键已删

    def test_delete_soft_deletes_entity_and_documents(self):
        """删除：IndexMetadata/Document 软删墓碑 + 向量硬删 + 会话选库清理。"""
        IndexMetadata.objects.create(user=self.user, name=f"user_{self.user.id}_kb-del")
        self._create_document(
            IndexMetadata.objects.get(name=f"user_{self.user.id}_kb-del"), "x.md"
        )

        with mock.patch(f"{_CROSS_APP}.clear_knowledge_base_selection") as clear_mock:
            delete_knowledge_base(self.user, "kb-del")

        self.manager_cls.return_value.delete_index.assert_called_once_with(f"user_{self.user.id}_kb-del")

        tomb = IndexMetadata.all_objects.get(name=f"user_{self.user.id}_kb-del")
        self.assertTrue(tomb.is_deleted)
        self.assertIsNotNone(tomb.deleted_at)
        self.assertTrue(Document.all_objects.get(filename="x.md").is_deleted)

        self.assertEqual(list_knowledge_bases(self.user), [])  # 删除后列表不可见
        clear_mock.assert_called_once_with(user_id=self.user.id, kb_name="kb-del")
        self.invalidate_cache.assert_called_once_with(
            user_id=self.user.id, user_index_name=f"user_{self.user.id}_kb-del"
        )

    def test_recreate_after_delete_restores_tombstone(self):
        """同名重建：命中墓碑 → 恢复实体而非 duplicate，状态与块数镜像归零。"""
        meta = IndexMetadata.objects.create(
            user=self.user,
            name=f"user_{self.user.id}_kb-r",
            num_documents=5,
            is_deleted=True,
            deleted_at=timezone.now(),
        )

        result = create_knowledge_base(self.user, "kb-r", "新描述")

        self.assertFalse(result["existing"])
        meta.refresh_from_db()
        self.assertFalse(meta.is_deleted)
        self.assertIsNone(meta.deleted_at)
        self.assertEqual(meta.status, IndexMetadata.IndexStatus.EMPTY)
        self.assertEqual(meta.num_documents, 0)
        self.assertEqual(meta.description, "新描述")
        self.manager_cls.return_value.create_empty_index.assert_called_once_with(
            name=f"user_{self.user.id}_kb-r", description="新描述"
        )
