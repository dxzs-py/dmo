"""DELETE /chat/sessions/<session_id>/messages/ 清空会话消息测试（ARCH-18）。

背景（ARCH-18）：
- 前端 /clear 命令调用 DELETE /chat/sessions/<sid>/messages/，
  但该集合路径此前仅注册 POST（ChatMessageCreateView），运行时 405；
- 修复：ChatMessageCreateView 补 delete 方法，软删除全部消息 +
  checkpoint 重建 + 广播 MESSAGES_DELETED，语义对齐单条/链式删除。

覆盖：
- 清空成功：200 + 全部消息软删除（记录保留可审计）
- 空会话：200 + deleted_message_ids 为空
- 他人会话：404（get_user_session 按属主过滤，不泄露存在性）
- 未认证：401

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_clear_session_messages --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.chat.models import ChatMessage, ChatSession

CHAT_URL = "/api/v1/chat/"


class ClearSessionMessagesTests(TestCase):
    """ChatMessageCreateView.delete 清空会话消息行为。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="clear-msg", password="pw123456")
        self.other = get_user_model().objects.create_user(username="clear-other", password="pw123456")
        self.client.force_authenticate(user=self.user)
        self.session = ChatSession.objects.create(user=self.user, title="s", session_id="sess-clear")
        ChatMessage.objects.create(session=self.session, role="user", content="hello")
        ChatMessage.objects.create(session=self.session, role="assistant", content="hi")

    def _url(self, session_id):
        return f"{CHAT_URL}sessions/{session_id}/messages/"

    def test_clear_deletes_all_messages(self):
        """清空成功：200 信封 + 全部消息软删除（记录保留）。"""
        resp = self.client.delete(self._url(self.session.session_id))
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(len(body["data"]["deleted_message_ids"]), 2)
        self.assertFalse(ChatMessage.objects.filter(session=self.session, is_deleted=False).exists())
        # 软删而非物理删：all_objects 仍可见（objects 默认排除软删记录）
        self.assertEqual(ChatMessage.all_objects.filter(session=self.session).count(), 2)

    def test_clear_empty_session_returns_empty_list(self):
        """空会话：200 + deleted_message_ids 为空（幂等）。"""
        empty = ChatSession.objects.create(user=self.user, title="empty", session_id="sess-empty")
        resp = self.client.delete(self._url(empty.session_id))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"]["deleted_message_ids"], [])

    def test_clear_other_user_session_returns_404(self):
        """他人会话：按属主过滤返回 404，不泄露会话存在性。"""
        other_session = ChatSession.objects.create(user=self.other, title="o", session_id="sess-other")
        resp = self.client.delete(self._url(other_session.session_id))
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_clear_requires_authentication(self):
        """未认证：401。"""
        anon = APIClient()
        resp = anon.delete(self._url(self.session.session_id))
        self.assertEqual(resp.status_code, 401, resp.content)
