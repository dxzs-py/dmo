"""knowledge 视图异常文案回归测试（dj-03 防回归样板）。

覆盖：
- 知识库列表接口异常路径经全局 handler 返回统一通用文案「服务器内部错误，请稍后重试」，
  不携带 str(e) 异常细节（内部细节仅进 logger.exception）
- 业务语义异常（FileNotFoundError「知识库不存在: xxx」）原样透传，不受影响

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.knowledge.tests.test_views_error_messages \
        --noinput --settings=Django_xm.settings.test
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.knowledge import views_kb

KNOWLEDGE_BASES_URL = "/api/v1/knowledge/knowledge-bases/"


class KnowledgeListErrorMessageTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="kbuser", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_list_exception_returns_generic_message(self):
        """系统异常 → 500 + 通用文案，不含异常细节。"""
        boom = RuntimeError("internal detail: FAISS index corrupted at D:\\secret\\path")
        with mock.patch.object(views_kb, "list_knowledge_bases", side_effect=boom):
            resp = self.client.get(KNOWLEDGE_BASES_URL)
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertEqual(body["message"], "服务器内部错误，请稍后重试")
        self.assertNotIn("internal detail", body["message"])

    def test_detail_not_found_passes_business_message(self):
        """业务异常（FileNotFoundError）→ 404 + 语义化中文消息原样透传。"""
        with mock.patch.object(
            views_kb,
            "get_knowledge_base_detail",
            side_effect=FileNotFoundError("知识库不存在: kb-x"),
        ):
            resp = self.client.get(f"{KNOWLEDGE_BASES_URL}kb-x/")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["message"], "知识库不存在: kb-x")


class KnowledgeRouteConvergenceTests(TestCase):
    """dj-07 路由收敛防回归：indices/ 旧路由与 upload 动词路由必须 404。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="kbroute", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_legacy_indices_list_returns_404(self):
        """GET /knowledge/indices/（已删除）→ 404。"""
        resp = self.client.get("/api/v1/knowledge/indices/")
        self.assertEqual(resp.status_code, 404)

    def test_legacy_upload_verb_route_returns_404(self):
        """POST /knowledge/knowledge-bases/{kb_id}/upload/（已删除）→ 404。"""
        resp = self.client.post("/api/v1/knowledge/knowledge-bases/xxx/upload/")
        self.assertEqual(resp.status_code, 404)
