"""ChatSessionListView 分页语义测试（dj-05 回归：缓存分支 page=1 硬编码）。

覆盖：
- 缓存命中分支按请求 page 正确分页（page=2 返回第二页，不再恒返第一页）
- 缓存分支与 DB 分支分页字段（items/total/page/page_size/total_pages）逐字段一致
- 非法 page（非数字）回退第 1 页，与 DB 分支一致
- 越界 page（>总页数、<1）回退最后一页，与 DB 分支 Django Paginator get_page 语义一致

实现说明：
- SecureSessionCacheService 三个方法全部 mock（get_user_sessions_list /
  get_cached_sessions_batch / cache_session），与缓存后端（dev=Redis /
  test=LocMem）解耦，测试聚焦视图分页逻辑本身；
- DB 分支期望值通过真实 ORM 请求获取（缓存 mock 为空强制走 DB 分支），
  再以镜像数据切入缓存分支，直接断言两分支输出对齐。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_chat_session_list_pagination
"""

import os
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.cache_manager.services.secure_session_cache import SecureSessionCacheService
from Django_xm.apps.chat.models import ChatSession

SESSIONS_URL = "/api/v1/chat/sessions/"
SESSION_COUNT = 25
# updated_at 基准：sess-00 最旧 → sess-24 最新（每条间隔 1 分钟）
UPDATED_AT_BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _updated_at(index):
    return UPDATED_AT_BASE + timedelta(minutes=index)


def _expected_page_ids(page, page_size=10, count=SESSION_COUNT):
    """按 updated_at 降序的 session_id 全序列切片（两分支共同的期望页内容）。"""
    ordered = [f"sess-{i:02d}" for i in range(count - 1, -1, -1)]
    start = (page - 1) * page_size
    return ordered[start : start + page_size]


class ChatSessionListPaginationTests(TestCase):
    """dj-05：ChatSessionListView 缓存分支与 DB 分支分页语义对齐。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="pager", password="pw123456")
        self.client.force_authenticate(user=self.user)

        # mock 缓存服务：默认空（强制 DB 分支），与缓存后端实现解耦
        patchers = [
            mock.patch.object(SecureSessionCacheService, "get_user_sessions_list", return_value=[]),
            mock.patch.object(SecureSessionCacheService, "get_cached_sessions_batch", return_value=[]),
            mock.patch.object(SecureSessionCacheService, "cache_session", return_value=True),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _create_sessions(user, count=SESSION_COUNT):
        """创建 count 个会话并显式设定递增 updated_at（绕过 auto_now）。"""
        for i in range(count):
            s = ChatSession.objects.create(user=user, title=f"s{i:02d}", session_id=f"sess-{i:02d}")
            ChatSession.objects.filter(pk=s.pk).update(updated_at=_updated_at(i))

    def _seed_cache_with_mirror_data(self):
        """把与 DB 会话镜像同构的数据注入缓存 mock，切换为缓存命中分支。"""
        cache_data = [
            {
                "session_id": f"sess-{i:02d}",
                "title": f"s{i:02d}",
                "updated_at": _updated_at(i).isoformat(),
            }
            for i in range(SESSION_COUNT)
        ]
        SecureSessionCacheService.get_user_sessions_list.return_value = [d["session_id"] for d in cache_data]
        # 每次注入新列表，避免视图原地 sort 污染后续请求的期望
        SecureSessionCacheService.get_cached_sessions_batch.side_effect = lambda user_id, ids: list(cache_data)

    def _get_list(self, query):
        resp = self.client.get(f"{SESSIONS_URL}{query}")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()["data"]

    def test_cache_branch_page2_matches_db_branch(self):
        """缓存命中 page=2 返回第二页，分页字段与 DB 分支逐字段一致。"""
        self._create_sessions(self.user)
        db_data = self._get_list("?page=2&page_size=10")

        self.assertEqual(db_data["page"], 2)
        self.assertEqual(db_data["total"], SESSION_COUNT)
        self.assertEqual(db_data["total_pages"], 3)
        self.assertEqual([it["session_id"] for it in db_data["items"]], _expected_page_ids(2))

        # 切换缓存命中：镜像数据请求同一页
        self._seed_cache_with_mirror_data()
        cache_data = self._get_list("?page=2&page_size=10")

        self.assertEqual(cache_data["page"], db_data["page"])
        self.assertEqual(cache_data["total"], db_data["total"])
        self.assertEqual(cache_data["page_size"], db_data["page_size"])
        self.assertEqual(cache_data["total_pages"], db_data["total_pages"])
        self.assertEqual(
            [it["session_id"] for it in cache_data["items"]],
            [it["session_id"] for it in db_data["items"]],
            "缓存分支 page=2 不得再恒返第一页（dj-05）",
        )

    def test_cache_branch_default_page_is_first(self):
        """缺省 page 参数回退第 1 页（最新 10 条）。"""
        self._create_sessions(self.user)
        self._seed_cache_with_mirror_data()

        data = self._get_list("?page_size=10")

        self.assertEqual(data["page"], 1)
        self.assertEqual(data["total"], SESSION_COUNT)
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual([it["session_id"] for it in data["items"]], _expected_page_ids(1))

    def test_cache_branch_invalid_page_falls_back_to_first(self):
        """非法 page（非数字）回退第 1 页，与 DB 分支一致。"""
        self._create_sessions(self.user)
        self._seed_cache_with_mirror_data()

        data = self._get_list("?page=abc&page_size=10")

        self.assertEqual(data["page"], 1)
        self.assertEqual([it["session_id"] for it in data["items"]], _expected_page_ids(1))

        # DB 分支同样回退第 1 页
        SecureSessionCacheService.get_user_sessions_list.return_value = []
        SecureSessionCacheService.get_cached_sessions_batch.side_effect = None
        SecureSessionCacheService.get_cached_sessions_batch.return_value = []
        db_data = self._get_list("?page=abc&page_size=10")
        self.assertEqual(db_data["page"], 1)

    def test_out_of_range_page_clamped_to_last_page_both_branches(self):
        """越界 page 回退最后一页（Django Paginator get_page 语义），两分支一致。"""
        self._create_sessions(self.user)

        # DB 分支：page=99 → 第 3 页；page=0（<1）→ 第 3 页
        db_over = self._get_list("?page=99&page_size=10")
        self.assertEqual(db_over["page"], 3)
        self.assertEqual(len(db_over["items"]), 5)
        db_zero = self._get_list("?page=0&page_size=10")
        self.assertEqual(db_zero["page"], 3)

        # 缓存分支同语义
        self._seed_cache_with_mirror_data()
        cache_over = self._get_list("?page=99&page_size=10")
        self.assertEqual(cache_over["page"], 3)
        self.assertEqual(cache_over["total_pages"], 3)
        self.assertEqual(
            [it["session_id"] for it in cache_over["items"]],
            [it["session_id"] for it in db_over["items"]],
        )
        cache_zero = self._get_list("?page=0&page_size=10")
        self.assertEqual(cache_zero["page"], 3)

    def test_page_size_shared_between_branches(self):
        """page_size 解析两分支共享：自定义 page_size 下分页字段一致。"""
        self._create_sessions(self.user)
        db_data = self._get_list("?page=2&page_size=7")

        self.assertEqual(db_data["page_size"], 7)
        self.assertEqual(db_data["total_pages"], 4)
        self.assertEqual([it["session_id"] for it in db_data["items"]], _expected_page_ids(2, page_size=7))

        self._seed_cache_with_mirror_data()
        cache_data = self._get_list("?page=2&page_size=7")

        self.assertEqual(cache_data["page_size"], 7)
        self.assertEqual(cache_data["total_pages"], 4)
        self.assertEqual(
            [it["session_id"] for it in cache_data["items"]],
            [it["session_id"] for it in db_data["items"]],
        )
