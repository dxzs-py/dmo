"""chat app 路由规范化防回归测试（dj-16）。

覆盖：
- 旧动词路由已死：DELETE /messages/<id>/delete/、DELETE /sessions/<sid>/messages/pair/delete/
  均不再匹配任何路由 → 404
- 消息单条删除 REST 通道（WS 化遗留）已下线：DELETE /messages/<id>/ → 405
  （资源明细路由保留承载 PATCH 更新，DELETE 不再由视图分发）
- 新路由 name 可 reverse 解析；旧 name（message_delete / message_pair_delete）已注销

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_route_normalization --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIClient

CHAT_URL = "/api/v1/chat/"


class ChatRouteNormalizationTests(TestCase):
    """dj-16：chat 旧动词删除路由移除，消息单条删除 REST 通道下线。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="chat-route", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_old_message_delete_route_unavailable(self):
        """旧动词删除路由不可达 → 404。"""
        resp = self.client.delete(f"{CHAT_URL}messages/1/delete/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_old_pair_delete_route_unavailable(self):
        """旧 body 传参的成对删除路由不可达 → 404。"""
        resp = self.client.delete(f"{CHAT_URL}sessions/some-session/messages/pair/delete/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_message_delete_rest_channel_removed(self):
        """DELETE /messages/<id>/ → 405：路由仍在（承载 PATCH），删除方法不再分发。"""
        url = reverse("chat:chat_messages_detail", kwargs={"message_id": 1})
        self.assertEqual(url, f"{CHAT_URL}messages/1/")
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 405, resp.content)

    def test_new_route_names_resolvable(self):
        """新路由 name 可 reverse 解析（路由已注册）。"""
        self.assertEqual(
            reverse("chat:chat_messages_pair_delete", kwargs={"session_id": "s1", "user_message_id": 1}),
            f"{CHAT_URL}sessions/s1/messages/pair/1/",
        )

    def test_old_route_names_removed(self):
        """旧路由 name 已注销，reverse 抛 NoReverseMatch。"""
        cases = [
            ("chat:message_delete", {"message_id": 1}),
            ("chat:message_pair_delete", {"session_id": "s1"}),
        ]
        for name, kwargs in cases:
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(name, kwargs=kwargs)
