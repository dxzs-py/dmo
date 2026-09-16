"""chat app 路由规范化防回归测试（dj-16）。

覆盖：
- 旧动词路由已死：DELETE /messages/<id>/delete/、DELETE /sessions/<sid>/messages/pair/delete/
  均不再匹配任何路由 → 404
- 新资源路由可达：DELETE /messages/<id>/ 路由匹配成功并进入视图
  （空库下消息不存在，视图返回 404 + 业务码 40401 信封；路由级 404 不携带该信封，
  以此证明请求已到达 ChatMessageDeleteView.delete）
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

# ErrorCode.NOT_FOUND 的业务码（视图级 404 信封标识，区别于路由级 404）
NOT_FOUND_CODE = 40401


class ChatRouteNormalizationTests(TestCase):
    """dj-16：chat 旧动词删除路由移除，新资源路由注册且 DELETE 可达视图。"""

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

    def test_new_message_delete_route_reaches_view(self):
        """DELETE /messages/<id>/ 路由匹配成功并进入视图（消息不存在 → 视图级 404 信封）。"""
        url = reverse("chat:chat_messages_delete", kwargs={"message_id": 1})
        self.assertEqual(url, f"{CHAT_URL}messages/1/")
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertEqual(resp.json()["code"], NOT_FOUND_CODE)

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
