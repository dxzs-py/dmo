"""R-UX-07：重建索引用户侧入口（KnowledgeBaseRebuildView）测试。

缺陷（修复前）：后端具备 rebuild_index_from_source_files（kb_service，digest
在档），但无用户侧 HTTP 入口——重建仅 ai_engine 管理设置页可达（admin），
E5-04 场景（向量删除失败/库残留）下普通用户无任何自救路径。

修复契约（本文件锁定）：
1. 路由 POST /knowledge/knowledge-bases/{kb_id}/rebuild/（IsAuthenticated）；
2. 编排完整复用 kb_service.rebuild_index_from_source_files——view 层用
   build_user_index_name（D-1 单源）构造索引全名，归属过滤在 service 层
   （IndexMetadata.filter(user=user, name=...).first() → 他人知识库 404）；
3. 重活经 sync_to_async(thread_sensitive=False) 调度（RP-01 同款，不占主执行器）；
4. FileNotFoundError → 404；ValueError（原始文件全部缺失）→ 400 INVALID_PARAMS；
5. 重建成功 message="索引重建成功"。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_kb_rebuild_view --noinput
"""

import asyncio
import inspect
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from Django_xm.apps.knowledge import views_kb

_VIEWS_KB = "Django_xm.apps.knowledge.views_kb"

_FAKE_USER = SimpleNamespace(
    is_authenticated=True, id=7, pk=7, username="tester", email="t@t.com"
)


def _rebuild_request(authenticated=True, kb_id="kb1"):
    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/knowledge/knowledge-bases/{kb_id}/rebuild/")
    if authenticated:
        force_authenticate(request, user=_FAKE_USER)
    return request


def _call_view(request, kb_id="kb1"):
    response = asyncio.run(views_kb.KnowledgeBaseRebuildView.as_view()(request, kb_id=kb_id))
    return response


class RebuildViewContractTests(SimpleTestCase):
    def test_handler_is_async(self):
        """RP-01 同款机制断言：重活型视图必须 async 形态。"""
        self.assertTrue(inspect.iscoroutinefunction(views_kb.KnowledgeBaseRebuildView.post))
        self.assertTrue(views_kb.KnowledgeBaseRebuildView.view_is_async)

    def test_route_registered(self):
        """路由接线：reverse 可解析（URL 拼写/注册回归）。"""
        url = reverse("knowledge:knowledge_bases_rebuild", kwargs={"kb_id": "kb1"})
        self.assertEqual(url, "/api/v1/knowledge/knowledge-bases/kb1/rebuild/")

    def test_unauthenticated_rejected(self):
        """未认证 401（IsAuthenticated）。"""
        response = _call_view(_rebuild_request(authenticated=False))
        self.assertEqual(response.status_code, 401)

    def test_success_calls_service_with_user_index_name(self):
        """成功：service 收到 (user, user_7_kb1) 全名，message 为重建成功。"""
        with mock.patch(f"{_VIEWS_KB}.rebuild_index_from_source_files") as rebuild:
            response = _call_view(_rebuild_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 200)
        self.assertEqual(response.data["message"], "索引重建成功")
        rebuild.assert_called_once_with(_FAKE_USER, "user_7_kb1")

    def test_not_found_translated_to_404(self):
        """service FileNotFoundError（归属过滤/不存在）→ 404。"""
        with mock.patch(
            f"{_VIEWS_KB}.rebuild_index_from_source_files",
            side_effect=FileNotFoundError("知识库不存在: user_7_kb1"),
        ):
            response = _call_view(_rebuild_request())

        self.assertEqual(response.status_code, 404)

    def test_value_error_translated_to_400(self):
        """原始文件全部缺失（ValueError，确定性业务错误）→ 400。"""
        with mock.patch(
            f"{_VIEWS_KB}.rebuild_index_from_source_files",
            side_effect=ValueError("无法从原始文件加载任何文档: user_7_kb1"),
        ):
            response = _call_view(_rebuild_request())

        self.assertEqual(response.status_code, 400)

    def test_unexpected_error_propagates_as_500(self):
        """非确定性异常（embedding/向量库故障）→ 500（不误报 4xx，全局 handler 兜底）。"""
        with mock.patch(
            f"{_VIEWS_KB}.rebuild_index_from_source_files",
            side_effect=RuntimeError("vector store unavailable"),
        ):
            response = _call_view(_rebuild_request())

        self.assertEqual(response.status_code, 500)


if __name__ == "__main__":
    import unittest

    unittest.main()
