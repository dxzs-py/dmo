"""ChatSessionListView N+1 查询回归断言（SubTask 12.3）。

对应 spec `harden-test-coverage-and-code-quality` Scenario: chat session list 无 N+1。
验证 `ChatSessionListView.get` 的 `select_related("user")` +
`annotate(message_count=Count("messages"))` 生效，list 端点查询数不随
session 数量线性增长。

mock 策略：
- patch `SecureSessionCacheService.get_user_sessions_list` 返回 None，
  强制走 DB 路径（缓存命中时不查 DB，无法验证 N+1）
- patch `SecureSessionCacheService.cache_session` 避免缓存写入副作用
- patch `SecureSessionCacheService.get_cached_sessions_batch` 避免缓存命中

运行方式：
    cd backend/Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/chat/tests/test_chat_session_list_n_plus_1.py -v
"""

from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from Django_xm.apps.chat.models import ChatMessage, ChatSession, MessageRole
from Django_xm.apps.users.models import User


class ChatSessionListNPlusOneTests(APITestCase):
    """ChatSessionListView list 端点 N+1 查询回归断言。

    ChatSessionListSerializer 的 fields 仅含 id/session_id/title/mode/
    selected_knowledge_base(s)/message_count/created_at/updated_at，
    不含 research_task_deleted / attachments（仅 detail serializer 有）。
    message_count 通过 annotate 提供，user 通过 select_related 提供，
    故 list 端点应无 N+1。
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="session-list-n1-tester",
            password="testpass123",
        )

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.user)

    def _make_sessions(self, count, messages_per_session=3):
        """批量创建 ChatSession + ChatMessage，模拟真实 list 场景。"""
        for i in range(count):
            session = ChatSession.objects.create(user=self.user)
            for j in range(messages_per_session):
                ChatMessage.objects.create(
                    session=session,
                    role=MessageRole.ASSISTANT if j % 2 else MessageRole.USER,
                    content=f"msg-{i}-{j}",
                )

    @patch("Django_xm.apps.chat.views_chat.SecureSessionCacheService.cache_session")
    @patch("Django_xm.apps.chat.views_chat.SecureSessionCacheService.get_cached_sessions_batch")
    @patch("Django_xm.apps.chat.views_chat.SecureSessionCacheService.get_user_sessions_list", return_value=None)
    def test_list_no_n_plus_1(self, _mock_get_list, _mock_get_batch, _mock_cache):
        """list 端点查询数不随 session 数量增长。

        创建 3 个 session（各 3 条消息）与 6 个 session（各 3 条消息），
        断言两次 GET 的查询数相等（N+1 的本质是查询数随数据量增长）。
        """
        self._make_sessions(3, messages_per_session=3)

        with CaptureQueriesContext(connection) as ctx_3:
            resp = self.client.get("/api/v1/chat/sessions/")
        self.assertEqual(resp.status_code, 200)
        queries_3 = len(ctx_3)

        self._make_sessions(3, messages_per_session=3)  # 再加 3 个，共 6 个

        with CaptureQueriesContext(connection) as ctx_6:
            resp = self.client.get("/api/v1/chat/sessions/")
        self.assertEqual(resp.status_code, 200)
        queries_6 = len(ctx_6)

        self.assertEqual(
            queries_3,
            queries_6,
            f"N+1 回归：3 sessions {queries_3} 查询 vs 6 sessions {queries_6} 查询，"
            "select_related('user') / annotate(message_count) 可能失效",
        )

    @patch("Django_xm.apps.chat.views_chat.SecureSessionCacheService.cache_session")
    @patch("Django_xm.apps.chat.views_chat.SecureSessionCacheService.get_cached_sessions_batch")
    @patch("Django_xm.apps.chat.views_chat.SecureSessionCacheService.get_user_sessions_list", return_value=None)
    def test_list_query_count_upper_bound(self, _mock_get_list, _mock_get_batch, _mock_cache):
        """list 端点查询数绝对上限断言。

        select_related + annotate 生效时，list 端点仅需：
        - 1 次 COUNT（paginate_queryset 总数）
        - 1 次 SELECT（session + select_related user + annotate message_count）
        留余量至 5，超过则视为 N+1 回归。
        """
        self._make_sessions(5, messages_per_session=4)

        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/api/v1/chat/sessions/")
        self.assertEqual(resp.status_code, 200)

        # 验证 message_count 已正确 annotate（非 0）
        body = resp.json()
        items = body.get("data", {}).get("items", [])
        self.assertTrue(len(items) > 0, "list 端点应返回 session")
        self.assertTrue(
            all(item.get("message_count", 0) > 0 for item in items),
            "message_count 应通过 annotate 正确计算",
        )

        self.assertLessEqual(
            len(ctx),
            5,
            f"list 端点查询数 {len(ctx)} 超过上限 5，疑似 N+1 回归。"
            f"SQL: {[q['sql'][:120] for q in ctx.captured_queries]}",
        )
