"""3.2 IndexManager 归属过滤越权单测。

背景：load_metadata/_detect_store_type/index_exists 原先只按 name 查询，
多租户隔离完全依赖 user_{id}_ 命名约定（构造方自觉），任何拿到他人完整
内部索引名（user_2_xxx）的调用方都能直接探测/读取。修复后三方法支持
user_id 归属校验：前缀守卫（user_{user_id}_ 硬边界）+ DB 查询按 user
归属过滤（_scoped_metadata_qs，防御前缀与归属不一致）。

覆盖：
- 三方法 × 越权拒绝 / 归属放行 矩阵
- 历史遗留无主行（user=NULL）对前缀匹配用户仍可用（兼容语义）
- user_id=None 内部可信路径行为不变
- 完整链路：kb_service.search_knowledge_base 经 index_exists 的越权拒绝

运行（backend/Django_xm 目录，conda env langchain_xm，DB_NAME=langchain_verify）：
    python manage.py test Django_xm.apps.knowledge.tests.test_index_permission_filter --noinput
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.apps.knowledge.models import IndexMetadata
from Django_xm.apps.knowledge.services import kb_service
from Django_xm.apps.knowledge.services.index_service import IndexManager

_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"
# search_knowledge_base 内为函数级导入（from .retrieval_service import ...），
# patch 必须落在源模块
_RETRIEVAL_SERVICE = "Django_xm.apps.knowledge.services.retrieval_service"


class IndexPermissionFilterTests(TestCase):
    """三方法归属矩阵：越权拒绝 / 归属放行 / 遗留无主行 / 内部路径。"""

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.owner = user_model.objects.create_user(username="owner312", password="pw123456")
        cls.attacker = user_model.objects.create_user(username="attacker312", password="pw123456")
        cls.manager = IndexManager()
        cls.owner_meta = IndexMetadata.objects.create(
            user=cls.owner, name=f"user_{cls.owner.id}_kb-own", store_type="pgvector", num_documents=3
        )
        cls.victim_meta = IndexMetadata.objects.create(
            user=cls.owner, name=f"user_{cls.owner.id}_victim", store_type="pgvector", num_documents=5
        )

    def test_index_exists_denies_foreign_prefix(self):
        """越权：user_2 持有的索引，user_1 即使知道完整内部名（user_2_xxx）→ False。

        直接以他人全名探测（攻击者已知内部命名约定），前缀守卫拒绝，
        不泄露存在性。
        """
        victim_index_name = f"user_{self.owner.id}_victim"
        self.assertFalse(self.manager.index_exists(victim_index_name, user_id=self.attacker.id))

    def test_index_exists_denies_foreign_owned_row(self):
        """越权：名字带 user_{attacker}_ 前缀但 DB 行归属他人 → DB 过滤拒绝。"""
        hijack_row = IndexMetadata.objects.create(
            user=self.owner, name=f"user_{self.attacker.id}_hijack2", num_documents=9
        )
        try:
            self.assertFalse(self.manager.index_exists(f"user_{self.attacker.id}_hijack2", user_id=self.attacker.id))
        finally:
            hijack_row.delete()

    def test_index_exists_allows_owner(self):
        """归属放行：本人在自己前缀索引上 exists 为 True。"""
        self.assertTrue(self.manager.index_exists(f"user_{self.owner.id}_kb-own", user_id=self.owner.id))

    def test_load_metadata_denies_foreign_prefix(self):
        """越权 load_metadata：非本人前缀 → None（等同不存在）。"""
        self.assertIsNone(self.manager.load_metadata(f"user_{self.owner.id}_victim", user_id=self.attacker.id))

    def test_load_metadata_allows_owner(self):
        """归属放行：本人可读自己的元数据。"""
        metadata = self.manager.load_metadata(f"user_{self.owner.id}_kb-own", user_id=self.owner.id)
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["num_documents"], 3)

    def test_detect_store_type_denies_foreign_prefix(self):
        """越权 _detect_store_type：不做任何探测，返回默认存储类型。"""
        store_type = self.manager._detect_store_type(f"user_{self.owner.id}_victim", user_id=self.attacker.id)
        from Django_xm.apps.knowledge.config import settings as app_settings

        self.assertEqual(store_type, app_settings.vector_store_type)

    def test_detect_store_type_allows_owner(self):
        """归属放行：本人探测得到 DB 声明的 store_type。"""
        store_type = self.manager._detect_store_type(f"user_{self.owner.id}_kb-own", user_id=self.owner.id)
        self.assertEqual(store_type, "pgvector")

    def test_legacy_null_user_row_accessible_to_prefix_owner(self):
        """遗留无主行（user=NULL）：前缀匹配用户仍可访问（升级兼容语义）。"""
        legacy = IndexMetadata.objects.create(user=None, name=f"user_{self.owner.id}_legacy", num_documents=1)
        try:
            self.assertTrue(self.manager.index_exists(f"user_{self.owner.id}_legacy", user_id=self.owner.id))
            self.assertIsNotNone(self.manager.load_metadata(f"user_{self.owner.id}_legacy", user_id=self.owner.id))
        finally:
            legacy.delete()

    def test_none_user_id_keeps_trusted_path(self):
        """user_id=None（系统内部可信路径）：行为与修复前一致，不做归属过滤。"""
        self.assertTrue(self.manager.index_exists(f"user_{self.owner.id}_victim"))
        self.assertIsNotNone(self.manager.load_metadata(f"user_{self.owner.id}_victim"))


class PermissionChainTests(TestCase):
    """完整链路：kb_service → IndexManager（带 user_id）的越权拒绝。"""

    def setUp(self):
        user_model = get_user_model()
        self.u1 = user_model.objects.create_user(username="chain_u1_312", password="pw123456")
        self.u2 = user_model.objects.create_user(username="chain_u2_312", password="pw123456")
        # u2 拥有 kb-shared；u1 只知道 kb_id "kb-shared"（他人 kb_id）
        IndexMetadata.objects.create(user=self.u2, name=f"user_{self.u2.id}_kb-shared", num_documents=4)

    def test_search_other_users_kb_raises_not_found(self):
        """u1 用 u2 的 kb_id 搜索 → FileNotFoundError（kb_service 链路拒绝）。"""
        with (
            mock.patch(f"{_KB_SERVICE}.VectorSearchCacheService.get_cached_search", return_value=None),
            mock.patch(f"{_KB_SERVICE}.VectorSearchCacheService.cache_search_result"),
            self.assertRaises(FileNotFoundError),
        ):
            kb_service.search_knowledge_base(self.u1, "kb-shared", "查询", top_k=3)

    def test_search_own_kb_passes_user_id_to_manager(self):
        """归属链路：本人 kb 的 index_exists 调用携带真实 user_id（守卫生效前提）。"""
        IndexMetadata.objects.create(user=self.u1, name=f"user_{self.u1.id}_kb-shared", num_documents=2)
        with (
            mock.patch(f"{_KB_SERVICE}.VectorSearchCacheService.get_cached_search", return_value=None),
            mock.patch(f"{_KB_SERVICE}.VectorSearchCacheService.cache_search_result"),
            mock.patch(f"{_RETRIEVAL_SERVICE}.load_user_vector_store", return_value=None) as load_mock,
            self.assertRaises(FileNotFoundError),
        ):
            # load_user_vector_store 返回 None → 走 FileNotFoundError（索引构建失败语义）
            kb_service.search_knowledge_base(self.u1, "kb-shared", "查询", top_k=3)

        # 前置的 index_exists 已带 user_id 通过（否则同样抛 FileNotFoundError 但守卫未接线）
        self.assertEqual(load_mock.call_args.args, (self.u1.id, "kb-shared"))
