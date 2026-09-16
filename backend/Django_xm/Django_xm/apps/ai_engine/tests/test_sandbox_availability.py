"""sandbox-availability 端点测试（Spec: 沙箱执行加固 / 任务级开关 3.3）。

覆盖：
- 普通用户（IsAuthenticated）可访问：GET /ai-engine/sandbox-availability/ 返回 200
- 响应 data.sandbox_enabled 与后端 SANDBOX_CONFIG.ENABLED 一致
- 未认证访问返回 401

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_sandbox_availability
"""

import os

os.environ["DJANGO_SETTINGS_MODULE"] = "Django_xm.settings.test"
import django

django.setup()

from django.conf import settings as django_settings
from django.test import TestCase
from rest_framework.test import APIClient


class SandboxAvailabilityViewTests(TestCase):
    """GET /api/v1/ai-engine/sandbox-availability/ 契约。"""

    def setUp(self):
        self.client = APIClient()

    def test_unauthenticated_returns_401(self):
        """未认证：返回 401（IsAuthenticated 生效）。"""
        resp = self.client.get("/api/v1/ai-engine/sandbox-availability/")
        self.assertEqual(resp.status_code, 401)

    def test_authenticated_returns_sandbox_enabled(self):
        """普通用户可访问：返回 200，sandbox_enabled 与后端配置一致。"""
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username="sandbox-user", password="pw123456")
        self.client.force_authenticate(user)
        resp = self.client.get("/api/v1/ai-engine/sandbox-availability/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # success_response 统一 {code, message, data}，code 为 ErrorCode.SUCCESS(=200)
        self.assertEqual(body["code"], 200)
        expected = bool(django_settings.SANDBOX_CONFIG.get("ENABLED", False))
        # test.py 中 SANDBOX_CONFIG.ENABLED=False
        self.assertIs(body["data"]["sandbox_enabled"], expected)
        self.assertIs(body["data"]["sandbox_enabled"], False)
