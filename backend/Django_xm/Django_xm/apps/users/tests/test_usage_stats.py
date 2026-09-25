"""usage-stats 端点聚合测试（AC-6）。"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from Django_xm.apps.chat.models import ChatMessage, ChatSession
from django.test import TestCase

USAGE_URL = "/api/v1/users/usage-stats/"
User = get_user_model()


class UsageStatsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="p1234567")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        # 两个会话
        s1 = ChatSession.objects.create(session_id="s1", user=self.user, title="t1")
        s2 = ChatSession.objects.create(session_id="s2", user=self.user, title="t2")

        # 两条消息：同一会话、不同自然日，token_count 分别 100/200
        now = timezone.now()
        m1 = ChatMessage.objects.create(
            session=s1, role="user", content="q1", token_count=100
        )
        ChatMessage.objects.filter(pk=m1.pk).update(created_at=now - timedelta(days=1))
        m2 = ChatMessage.objects.create(
            session=s2, role="assistant", content="a1", token_count=200
        )
        ChatMessage.objects.filter(pk=m2.pk).update(created_at=now)

    def test_aggregated_stats(self):
        response = self.client.get(USAGE_URL)
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(data["total_sessions"], 2)
        self.assertEqual(data["total_messages"], 2)
        self.assertEqual(data["total_tokens"], 300)
        self.assertEqual(data["active_days"], 2)

    def test_cache_hit_second_request(self):
        # 第一次请求建立缓存
        self.client.get(USAGE_URL)
        # 新增一条消息：聚合结果应不变（命中缓存）
        s = ChatSession.objects.get(session_id="s1")
        ChatMessage.objects.create(session=s, role="user", content="extra")

        response = self.client.get(USAGE_URL)
        data = response.data["data"]
        self.assertEqual(data["total_messages"], 2)  # 缓存值

    def test_user_isolation(self):
        other = User.objects.create_user(username="u2", password="p1234567")
        s = ChatSession.objects.create(session_id="s3", user=other)
        ChatMessage.objects.create(session=s, role="user", content="x", token_count=999)

        other_client = APIClient()
        other_client.force_authenticate(other)
        response = other_client.get(USAGE_URL)
        data = response.data["data"]
        self.assertEqual(data["total_messages"], 1)
        self.assertEqual(data["total_tokens"], 999)

    def test_requires_authentication(self):
        anon = APIClient()
        result = anon.get(USAGE_URL)
        self.assertEqual(result.status_code, 401)
