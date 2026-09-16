"""cache_manager 视图测试（dj-01 权限收紧 + dj-03 异常文案回归）。

覆盖：
- CacheClearView 权限矩阵：普通用户 scope=all/model/pattern → 403；
  普通用户 scope=query → 200 且仅删除自身 rag_query 前缀；管理员 scope=all → 200
- scope 非法值 → 400（VALIDATION_FAILED）
- 异常路径经全局 handler 返回统一通用文案，不泄漏 str(e) 细节
- 旧参数键 type 已彻底移除：传 type 不再触发全量清除（等价于 scope 缺省 all 的权限判定）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.cache_manager.tests.test_views_clear \
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

from Django_xm.apps.cache_manager import views as cache_views

CLEAR_URL = "/api/v1/cache/clear/"


class CacheClearPermissionMatrixTests(TestCase):
    """dj-01：权限按 scope 动态分派。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="plainuser", password="pw123456")
        self.admin = get_user_model().objects.create_user(username="adminuser", password="pw123456", is_staff=True)

    def test_normal_user_scope_all_forbidden(self):
        """普通用户全量清缓存 → 403（修复前：一键清空全站缓存）。"""
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(CLEAR_URL, {"scope": "all"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_normal_user_scope_model_forbidden(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(CLEAR_URL, {"scope": "model"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_normal_user_scope_pattern_forbidden(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(CLEAR_URL, {"scope": "pattern", "pattern": "rag_query:*"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_normal_user_scope_query_allowed_and_scoped_to_self(self):
        """普通用户 scope=query → 200，且仅删除自身前缀 rag_query:user_{id}_*。"""
        self.client.force_authenticate(user=self.user)
        with mock.patch.object(cache_views.CacheService, "delete_pattern", return_value=1) as mock_delete:
            resp = self.client.post(CLEAR_URL, {"scope": "query"}, format="json")
        self.assertEqual(resp.status_code, 200)
        mock_delete.assert_called_once_with(f"rag_query:user_{self.user.id}_*")

    def test_normal_user_query_scope_with_pattern_bypass_blocked(self):
        """组合绕过（dj-01R 修复）：普通用户 {scope:query, pattern:*} 不得删任意 key。

        修复前 `if pattern:` 优先于 `elif scope == "query"`，普通用户携带 pattern
        会旁路权限执行 delete_pattern("*")；修复后 pattern 仅作为 scope="pattern"
        的配套参数，scope=query 请求忽略 pattern 仅删自身前缀。
        """
        self.client.force_authenticate(user=self.user)
        with mock.patch.object(cache_views.CacheService, "delete_pattern", return_value=1) as mock_delete:
            resp = self.client.post(CLEAR_URL, {"scope": "query", "pattern": "*"}, format="json")
        self.assertEqual(resp.status_code, 200)
        # 断言删除的是用户自己的前缀，而非传入的任意 pattern
        mock_delete.assert_called_once_with(f"rag_query:user_{self.user.id}_*")

    def test_admin_pattern_scope_allowed(self):
        """管理员 scope=pattern 携带 pattern → 200（pattern 分支可正常使用）。"""
        self.client.force_authenticate(user=self.admin)
        with mock.patch.object(cache_views.CacheService, "delete_pattern", return_value=3) as mock_delete:
            resp = self.client.post(CLEAR_URL, {"scope": "pattern", "pattern": "test:*"}, format="json")
        self.assertEqual(resp.status_code, 200)
        mock_delete.assert_called_once_with("test:*")
        self.assertEqual(resp.json()["data"]["cleared"], 1)

    def test_pattern_scope_missing_pattern_rejected(self):
        """scope=pattern 但缺 pattern 参数 → 400（配套参数校验）。"""
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(CLEAR_URL, {"scope": "pattern"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_admin_scope_all_allowed(self):
        """管理员全量清缓存 → 200（Redis 客户端不可用时跳过删除，仍成功）。"""
        self.client.force_authenticate(user=self.admin)
        with mock.patch.object(cache_views, "get_redis_client", return_value=None):
            resp = self.client.post(CLEAR_URL, {"scope": "all"}, format="json")
        self.assertEqual(resp.status_code, 200)

    def test_invalid_scope_rejected(self):
        """scope 非法值 → 400，不落入任何清除分支。"""
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(CLEAR_URL, {"scope": "bogus"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], 40002)

    def test_legacy_type_key_has_no_effect(self):
        """旧 type 键已移除：仅传 type（无 scope）对普通用户按 scope=all 判权 → 403。"""
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(CLEAR_URL, {"type": "all"}, format="json")
        self.assertEqual(resp.status_code, 403)


class CacheClearErrorMessageTests(TestCase):
    """dj-03：异常路径返回通用文案，不泄漏异常细节。"""

    def setUp(self):
        self.client = APIClient()
        self.admin = get_user_model().objects.create_user(username="adminuser", password="pw123456", is_staff=True)

    def test_exception_returns_generic_message(self):
        self.client.force_authenticate(user=self.admin)
        boom = RuntimeError("internal detail: connection refused to 10.0.0.1:6379")
        with mock.patch.object(cache_views.CacheService, "delete_pattern", side_effect=boom):
            resp = self.client.post(CLEAR_URL, {"scope": "pattern", "pattern": "x*"}, format="json")
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertEqual(body["message"], "服务器内部错误，请稍后重试")
        self.assertNotIn("internal detail", body["message"])
