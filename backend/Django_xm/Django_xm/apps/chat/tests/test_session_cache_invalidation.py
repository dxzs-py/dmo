"""会话缓存失效粒度测试（dj-09：单条目失效 + 列表短 TTL）。

背景：
- 修复前 on_session_save / on_session_delete 均调用 invalidate_all_user_sessions
  全量清空用户所有会话缓存，post_save 含每次更新（如 updated_at 触碰），
  高频全量清空导致缓存命中率趋零；
- 修复后：post_save 仅失效当前会话单条 key（invalidate_session_entry），
  post_delete 失效单条 key 并从列表索引 remove（invalidate_user_session），
  patch 重命名路径显式失效单条 key + 列表索引 key；
- 列表索引 key TTL 由 TIMEOUT*24（24h）收紧为 LIST_TIMEOUT（120s）。

覆盖：
- post_save（更新）仅失效当前会话单条 key，其它会话缓存与列表索引保留
- post_delete 单条 key 删除且从列表索引 remove，其它会话缓存保留
- patch 重命名路径单条 key 与列表索引 key 均被删，其它会话缓存保留
- cache_session 单条 key 用 TIMEOUT、列表索引 key 用 LIST_TIMEOUT

实现说明：
- test settings 的 default cache 为 LocMemCache，本测试用真实缓存操作
  （不 mock SecureSessionCacheService），直接断言 key 存活状态；
- post_delete 用例 mock schedule_ai_data_cleanup，避免 Celery EAGER 任务
  在 TestCase 事务内访问 checkpoint 数据引入不稳定。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_session_cache_invalidation --settings=Django_xm.settings.test
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.cache_manager.services import secure_session_cache
from Django_xm.apps.cache_manager.services.secure_session_cache import SecureSessionCacheService
from Django_xm.apps.chat.models import ChatSession

SESSIONS_URL = "/api/v1/chat/sessions/"


class SessionCacheInvalidationTestBase(TestCase):
    """dj-09 测试基类：单用户 + 两个会话，两个会话均写入缓存。"""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="dj09", password="pw123456")
        self.sess_a = ChatSession.objects.create(user=self.user, title="A", session_id="sess-a")
        self.sess_b = ChatSession.objects.create(user=self.user, title="B", session_id="sess-b")
        SecureSessionCacheService.cache_session(self.user.id, {"session_id": "sess-a", "title": "A"})
        SecureSessionCacheService.cache_session(self.user.id, {"session_id": "sess-b", "title": "B"})

    def _is_session_cached(self, session_id):
        """通过生产读取入口 get_cached_sessions_batch 判断单条会话是否在缓存。"""
        return bool(SecureSessionCacheService.get_cached_sessions_batch(self.user.id, [session_id]))

    def _assert_both_cached(self):
        """两个会话单条 key 与列表索引均在缓存中。"""
        self.assertTrue(self._is_session_cached("sess-a"))
        self.assertTrue(self._is_session_cached("sess-b"))
        self.assertEqual(SecureSessionCacheService.get_user_sessions_list(self.user.id), ["sess-a", "sess-b"])


class SessionSaveSignalCacheTests(SessionCacheInvalidationTestBase):
    """dj-09：on_session_save 改为仅失效当前会话单条 key。"""

    def test_post_save_update_invalidates_only_current_entry(self):
        """post_save（更新）→ 仅当前会话单条 key 失效，另一会话与列表索引保留。"""
        self._assert_both_cached()

        self.sess_a.title = "A-renamed"
        self.sess_a.save()  # 触发 post_save（created=False）

        self.assertFalse(
            self._is_session_cached("sess-a"),
            "被更新的会话单条 key 应失效",
        )
        self.assertTrue(
            self._is_session_cached("sess-b"),
            "未触碰的另一会话单条 key 不应失效（dj-09 根因：不再全量清空）",
        )
        self.assertEqual(
            SecureSessionCacheService.get_user_sessions_list(self.user.id),
            ["sess-a", "sess-b"],
            "列表索引 key 不应被 post_save 失效",
        )


class SessionDeleteSignalCacheTests(SessionCacheInvalidationTestBase):
    """dj-09：on_session_delete 改为 invalidate_user_session（单条 + 列表 remove）。"""

    def test_post_delete_invalidates_entry_and_removes_from_list(self):
        """post_delete → 单条 key 删除且从列表索引 remove，另一会话缓存保留。"""
        self._assert_both_cached()

        with mock.patch("Django_xm.apps.ai_engine.services.cross_app.schedule_ai_data_cleanup"):
            self.sess_a.delete()  # 触发 post_delete

        self.assertFalse(
            self._is_session_cached("sess-a"),
            "被删除会话的单条 key 应失效",
        )
        self.assertTrue(
            self._is_session_cached("sess-b"),
            "未删除的另一会话单条 key 不应失效",
        )
        self.assertEqual(
            SecureSessionCacheService.get_user_sessions_list(self.user.id),
            ["sess-b"],
            "列表索引应 remove 被删会话且保留其余会话",
        )


class ChatSessionDetailPatchCacheTests(SessionCacheInvalidationTestBase):
    """dj-09：patch 重命名路径显式失效单条 key + 列表索引 key。"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_patch_rename_invalidates_entry_and_list(self):
        """patch 重命名 → 当前会话单条 key 与列表索引 key 均被删，另一会话缓存保留。"""
        self._assert_both_cached()

        resp = self.client.patch(f"{SESSIONS_URL}sess-a/", {"title": "renamed"}, format="json")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"]["title"], "renamed")
        self.assertFalse(
            self._is_session_cached("sess-a"),
            "重命名会话的单条 key 应失效",
        )
        self.assertEqual(
            SecureSessionCacheService.get_user_sessions_list(self.user.id),
            [],
            "列表索引 key 应被删除，保证列表接口回源 DB 拿到新标题",
        )
        self.assertTrue(
            self._is_session_cached("sess-b"),
            "其它会话单条 key 不应被重命名操作波及",
        )


class CacheSessionTtlTests(TestCase):
    """dj-09：cache_session 单条 key 用 TIMEOUT，列表索引 key 用 LIST_TIMEOUT。"""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="dj09ttl", password="pw123456")

    def test_cache_session_uses_short_list_ttl(self):
        with mock.patch.object(secure_session_cache, "cache") as cache_mock:
            cache_mock.get.return_value = []  # 列表索引初始为空
            SecureSessionCacheService.cache_session(self.user.id, {"session_id": "sess-a", "title": "A"})

        set_timeouts = {call.args[0]: call.kwargs.get("timeout") for call in cache_mock.set.call_args_list}
        self.assertEqual(
            set_timeouts.get(f"user_session:{self.user.id}:sess-a"),
            SecureSessionCacheService.TIMEOUT,
            "单条会话 key 应保持 TIMEOUT=3600（红线：不动）",
        )
        self.assertEqual(
            set_timeouts.get(f"user_sessions_list:{self.user.id}"),
            SecureSessionCacheService.LIST_TIMEOUT,
            "列表索引 key 应使用短 TTL LIST_TIMEOUT=120（原 TIMEOUT*24=24h）",
        )
        self.assertEqual(SecureSessionCacheService.LIST_TIMEOUT, 120)
