"""ChatStreamView 会话属主校验测试（D-3 越权修复回归）。

背景（D-3）：
- ChatStreamView.post 此前以 `ChatSession.objects.get(session_id=session_id)`
  裸查询获取会话（无 user 过滤），重新生成校验与消息对创建均以该会话为
  属主上下文 → 用户 A 可对用户 B 的会话触发 stream（越权写他人会话）。
- 修复：改用 get_session_or_404(session_id, request.user)（user + is_deleted
  过滤），会话不存在或不属于当前用户一律 404，与本文件其余端点语义一致。

覆盖：
- 他人会话触发 stream：404（不泄露会话存在性），且不创建任何消息
- 他人会话触发重新生成：404（regenerate 分支同样被属主过滤拦截）
- 本人会话正常创建消息对（冒烟：属主过滤不误伤正常路径）
- 不存在的会话：404

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_stream_session_authz --noinput
"""

import os
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.chat.models import ChatMessage, ChatSession

STREAM_URL = "/api/v1/chat/stream/"


def _uuid():
    """ChatRequestSerializer 校验 session_id 为 UUID 格式，测试会话 ID 用 uuid4 生成"""
    return str(uuid.uuid4())


class ChatStreamSessionAuthzTests(TestCase):
    """ChatStreamView.post 会话属主过滤（D-3）。"""

    def setUp(self):
        self.client = APIClient()
        user_model = get_user_model()
        self.user_a = user_model.objects.create_user(username="stream-a", password="pw123456")
        self.user_b = user_model.objects.create_user(username="stream-b", password="pw123456")
        self.client.force_authenticate(user=self.user_a)
        self.session_b = ChatSession.objects.create(user=self.user_b, title="b", session_id=_uuid())
        self.session_a = ChatSession.objects.create(user=self.user_a, title="a", session_id=_uuid())

    def _post_stream(self, session_id, extra=None):
        payload = {"message": "hello", "session_id": session_id}
        if extra:
            payload.update(extra)
        return self.client.post(STREAM_URL, payload, format="json")

    def test_other_user_session_returns_404(self):
        """用户 A 对用户 B 的会话触发 stream：404，不创建消息。"""
        resp = self._post_stream(self.session_b.session_id)
        self.assertEqual(resp.status_code, 404, resp.content)
        # 越权请求不得在他人会话内留下任何消息写入
        self.assertEqual(ChatMessage.objects.filter(session=self.session_b).count(), 0)

    def test_other_user_session_regenerate_returns_404(self):
        """重新生成分支同样被属主过滤拦截：404。"""
        user_msg = ChatMessage.objects.create(session=self.session_b, role="user", content="q")
        ai_msg = ChatMessage.objects.create(session=self.session_b, role="assistant", content="a")
        resp = self._post_stream(
            self.session_b.session_id,
            extra={
                "regenerate": True,
                "user_message_id": str(user_msg.id),
                "assistant_message_id": str(ai_msg.id),
            },
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_nonexistent_session_returns_404(self):
        """不存在的会话：404（fail-closed，不再降级放行）。"""
        resp = self._post_stream(_uuid())
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_own_session_still_creates_message_pair(self):
        """本人会话正常路径不回归：创建 user/assistant 消息对并返回 started。"""
        resp = self._post_stream(self.session_a.session_id)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["status"], "started")
        roles = list(
            ChatMessage.objects.filter(session=self.session_a).order_by("created_at").values_list("role", flat=True)
        )
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
