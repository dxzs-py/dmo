"""PATCH /chat/messages/<id>/ 空正文保护（WS 断链导致正文被清空的回归测试）。

故障背景：WebSocket 断链期间前端收不到任何正文快照（正文快照
persist_history=False，不进回放历史），本地 message.content 为空；随后工具卡片
同步/流结束时仍会整条 PATCH，把后端已落库的正文清空（实测 1192 字被写成空，
刷新后不可恢复）。

契约：
- 入参 content 为空/空白且库中已有正文 → 忽略该字段，库中正文保持不变
- 入参 content 非空 → 正常覆盖
- 库中正文本就为空 → 允许写入空值（新建/空消息场景不受影响）

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.chat.tests.test_message_update_empty_content_guard
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.chat.models import ChatMessage, ChatSession

STORED_CONTENT = "已落库的正文：Redis 持久化与 RDB/AOF 对比"


class MessageUpdateEmptyContentGuardTests(TestCase):
    """空正文不得覆盖已持久化正文。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="msg-guard", password="pw123456")
        self.client.force_authenticate(user=self.user)
        self.session = ChatSession.objects.create(user=self.user, title="s", session_id="sess-guard")
        self.message = ChatMessage.objects.create(
            session=self.session,
            role="assistant",
            content=STORED_CONTENT,
            versions=[{"id": "v0", "content": STORED_CONTENT}],
            current_version=0,
        )

    def _patch(self, payload):
        return self.client.patch(
            f"/api/v1/chat/messages/{self.message.id}/", payload, format="json"
        )

    def test_empty_content_does_not_wipe_stored_content(self):
        """content='' → 忽略，库中正文保持不变。"""
        resp = self._patch({"content": ""})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.message.refresh_from_db()
        self.assertEqual(self.message.content, STORED_CONTENT)

    def test_blank_content_does_not_wipe_stored_content(self):
        """content 仅空白 → 同样视为空，不覆盖。"""
        resp = self._patch({"content": "   \n\t "})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.message.refresh_from_db()
        self.assertEqual(self.message.content, STORED_CONTENT)

    def test_non_empty_content_is_applied(self):
        """非空 content → 正常覆盖（行为不变）。"""
        resp = self._patch({"content": "新的正文"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.message.refresh_from_db()
        self.assertEqual(self.message.content, "新的正文")

    def test_other_fields_still_updated_when_content_dropped(self):
        """丢弃空 content 不影响同一次 PATCH 的其它字段（如 sources）。"""
        resp = self._patch({"content": "", "sources": [{"source": "a.md", "citation": 1}]})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.message.refresh_from_db()
        self.assertEqual(self.message.content, STORED_CONTENT)
        self.assertEqual(len(self.message.sources), 1)

    def test_empty_content_allowed_when_stored_is_empty(self):
        """库中正文本就为空时不受保护（空消息/新建场景不被误拦）。"""
        empty_msg = ChatMessage.objects.create(
            session=self.session, role="assistant", content=""
        )
        resp = self.client.patch(
            f"/api/v1/chat/messages/{empty_msg.id}/", {"content": ""}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        empty_msg.refresh_from_db()
        self.assertEqual(empty_msg.content, "")
