"""知识库创建/更新/检索校验规范化测试（dj-10）。

覆盖：
- POST 缺 name → 400 INVALID_PARAMS（serializer 统一校验，message 含字段名）
- POST 活跃同名向量索引 → 409 DUPLICATE_RESOURCE（KnowledgeBaseAlreadyExistsError 语义化分支，
  取代原「已存在」文案嗅探）
- POST 预置软删除 DB 记录 → 恢复成功 200，DB 记录 is_deleted 复位
- POST 预置活跃 DB 记录且向量索引缺失 → 200 且 data.existing=True（existing 字段显式透传，
  不再 pop 消费）
- PATCH 空 body → 200 不报错，服务层收到 description=""
- search 缺 query / top_k 越界 → 400 INVALID_PARAMS

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_kb_validation --settings=Django_xm.settings.test
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from Django_xm.apps.knowledge import views_kb
from Django_xm.apps.knowledge.models import DocumentIndex
from Django_xm.common.error_codes import ErrorCode

KNOWLEDGE_BASES_URL = "/api/v1/knowledge/knowledge-bases/"
SEARCH_URL = "/api/v1/knowledge/knowledge-bases/{kb_id}/search/"

_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"


class KnowledgeBaseCreateValidationTests(TestCase):
    """POST /knowledge/knowledge-bases/：serializer 校验 + 语义化异常分支。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="kbcreate", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_post_without_name_returns_400(self):
        """缺 name → 400 + INVALID_PARAMS + message 含字段名。"""
        resp = self.client.post(KNOWLEDGE_BASES_URL, {"description": "无名称"}, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], int(ErrorCode.INVALID_PARAMS))
        self.assertIn("name", body["message"])

    def test_post_duplicate_active_index_returns_409(self):
        """活跃同名向量索引 → 服务层抛 KnowledgeBaseAlreadyExistsError → 409 + DUPLICATE_RESOURCE。"""
        with mock.patch(f"{_KB_SERVICE}.IndexManager") as manager_cls:
            manager_cls.return_value.index_exists.return_value = True
            resp = self.client.post(KNOWLEDGE_BASES_URL, {"name": "dup-kb"}, format="json")
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["code"], int(ErrorCode.DUPLICATE_RESOURCE))
        self.assertEqual(body["message"], "知识库已存在: dup-kb")

    def test_post_with_soft_deleted_record_restores(self):
        """预置软删除 DB 记录 → 恢复路径 200，记录 is_deleted 复位。"""
        DocumentIndex.all_objects.create(
            user=self.user,
            index_name="restore-kb",
            description="旧描述",
            document_count=3,
            is_deleted=True,
            deleted_at=timezone.now(),
        )
        with (
            mock.patch(f"{_KB_SERVICE}.IndexManager") as manager_cls,
            mock.patch(f"{_KB_SERVICE}.invalidate_knowledge_cache"),
        ):
            manager_cls.return_value.index_exists.return_value = False
            resp = self.client.post(KNOWLEDGE_BASES_URL, {"name": "restore-kb", "description": "新描述"}, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["data"]["existing"])
        record = DocumentIndex.all_objects.get(user=self.user, index_name="restore-kb")
        self.assertFalse(record.is_deleted)
        self.assertIsNone(record.deleted_at)
        self.assertEqual(record.description, "新描述")
        self.assertEqual(record.document_count, 3)

    def test_post_with_active_db_record_returns_existing_true(self):
        """预置活跃 DB 记录且向量索引缺失 → 200 + data.existing=True 显式透传。"""
        DocumentIndex.objects.create(user=self.user, index_name="active-kb", description="既有", document_count=2)
        with (
            mock.patch(f"{_KB_SERVICE}.IndexManager") as manager_cls,
            mock.patch(f"{_KB_SERVICE}.invalidate_knowledge_cache"),
        ):
            manager_cls.return_value.index_exists.return_value = False
            resp = self.client.post(KNOWLEDGE_BASES_URL, {"name": "active-kb"}, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["message"], "知识库已存在")
        self.assertTrue(body["data"]["existing"])
        self.assertEqual(body["data"]["document_count"], 2)


class KnowledgeBaseUpdateValidationTests(TestCase):
    """PATCH /knowledge/knowledge-bases/{kb_id}/：serializer 校验。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="kbupdate", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_patch_empty_body_succeeds(self):
        """空 body → 200 不报错，服务层收到 description=""。"""
        with mock.patch.object(
            views_kb,
            "update_knowledge_base",
            return_value={"id": "kb1", "name": "kb1", "description": ""},
        ) as update_mock:
            resp = self.client.patch(f"{KNOWLEDGE_BASES_URL}kb1/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["message"], "知识库更新成功")
        update_mock.assert_called_once_with(self.user, "kb1", "")


class KnowledgeBaseSearchValidationTests(TestCase):
    """POST /knowledge/knowledge-bases/{kb_id}/search/：serializer 校验。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="kbsearch", password="pw123456")
        self.client.force_authenticate(user=self.user)
        self.search_url = SEARCH_URL.format(kb_id="kb1")

    def test_search_without_query_returns_400(self):
        """缺 query → 400 + INVALID_PARAMS。"""
        resp = self.client.post(self.search_url, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], int(ErrorCode.INVALID_PARAMS))
        self.assertIn("query", body["message"])

    def test_search_top_k_out_of_range_returns_400(self):
        """top_k 超上限 → 400 + INVALID_PARAMS（不触达服务层）。"""
        resp = self.client.post(self.search_url, {"query": "什么是 RAG", "top_k": 100}, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], int(ErrorCode.INVALID_PARAMS))
        self.assertIn("top_k", body["message"])
