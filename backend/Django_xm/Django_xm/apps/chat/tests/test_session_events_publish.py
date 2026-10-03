"""spec D6 事件断链补全：session_updated / message_regenerate_reverted 发布点单测。

覆盖：
- ChatSessionDetailView.patch 成功 → transaction.on_commit 后发布 SESSION_UPDATED
  （payload：id / session_id / fields 仅含本次变更字段 + updated_at）；
- revert_regenerate_archive：空版本回滚（弹出末尾空版本 + 快照恢复顶层字段）并发布
  MESSAGE_REGENERATE_REVERTED（payload：session_id / message_id / message 序列化），
  payload 通过 validate_payload schema 校验；
- 无可回滚空版本（单版本 / 末版本 content 非空）→ 不回滚不发布。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_session_events_publish
"""

import asyncio
import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.chat.models import ChatMessage, ChatSession, MessageRole
from Django_xm.apps.chat.services import stream_persistence as sp_module
from Django_xm.apps.chat.services.stream_persistence import (
    _revert_regenerate_archive_sync,
    revert_regenerate_archive,
)
from Django_xm.common.event_schema import EventType, validate_payload

_PATCH_PUBLISH_TARGET = "Django_xm.apps.chat.views_chat.publish_event_sync"
_REVERT_PUBLISH_TARGET = "Django_xm.common.realtime_events.publish_event"


class SessionUpdatedPublishTests(TestCase):
    """PATCH 会话 → on_commit 后发布 SESSION_UPDATED。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="d6pub", password="pw123456")
        ChatSession.objects.create(user=self.user, title="旧标题", session_id="sess-d6-pub")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_patch_publishes_session_updated_with_changed_fields(self):
        """改标题 → 发布 SESSION_UPDATED，fields 仅含 title + updated_at。"""
        with mock.patch(_PATCH_PUBLISH_TARGET) as publish_mock:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.patch(
                    "/api/v1/chat/sessions/sess-d6-pub/",
                    data={"title": "新标题"},
                    format="json",
                )

        self.assertEqual(response.status_code, 200)
        publish_mock.assert_called_once()
        args, kwargs = publish_mock.call_args
        event_type, payload = args[0], args[1]
        self.assertEqual(event_type, EventType.SESSION_UPDATED)
        self.assertEqual(payload["id"], "sess-d6-pub")
        self.assertEqual(payload["session_id"], "sess-d6-pub")
        self.assertEqual(payload["fields"]["title"], "新标题")
        self.assertIn("updated_at", payload["fields"])
        # 未变更字段不进 payload（fields 契约：仅本次变更字段 + updated_at）
        self.assertNotIn("selected_knowledge_base", payload["fields"])
        self.assertNotIn("selected_knowledge_bases", payload["fields"])
        self.assertEqual(kwargs, {"user_id": str(self.user.id)})


class RevertRegenerateArchiveSyncTests(TestCase):
    """_revert_regenerate_archive_sync DB 回滚行为（同步直调，共享测试线程 DB 连接）。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="d6rev", password="pw123456")
        self.session = ChatSession.objects.create(user=self.user, title="d6", session_id="sess-d6-rev")
        self.msg = ChatMessage.objects.create(
            session=self.session,
            role=MessageRole.ASSISTANT,
            content="",
            is_streaming=True,
            versions=[
                {
                    "content": "旧回答",
                    "sources": [{"title": "s1"}],
                    "plan": None,
                    "tool_calls": [],
                    "reasoning": None,
                    "suggestions": ["q1"],
                    "model": "gpt-x",
                    "created_at": "2026-01-01T00:00:00",
                },
                {"content": ""},
            ],
            current_version=1,
        )

    def test_reverts_empty_version_and_builds_message_data(self):
        """空版本回滚：弹出版本 + 快照恢复顶层字段 + 返回序列化 message。"""
        reverted, message_data = _revert_regenerate_archive_sync(self.session.session_id, str(self.msg.id))

        self.assertIsNotNone(reverted)
        self.msg.refresh_from_db()
        self.assertEqual(len(self.msg.versions), 1)
        self.assertEqual(self.msg.current_version, 0)
        self.assertEqual(self.msg.content, "旧回答")
        self.assertFalse(self.msg.is_streaming)
        self.assertEqual(self.msg.model, "gpt-x")

        # 回滚后的完整 message 序列化（前端 transformBackendMessageToFrontend 消费）
        self.assertEqual(message_data["content"], "旧回答")

    def test_no_empty_version_skips_revert(self):
        """单版本消息（无归档空版本）→ 不回滚。"""
        ChatMessage.objects.filter(pk=self.msg.pk).update(versions=[{"content": "仅一版"}], current_version=0)

        reverted, message_data = _revert_regenerate_archive_sync(self.session.session_id, str(self.msg.id))

        self.assertIsNone(reverted)
        self.assertIsNone(message_data)
        self.msg.refresh_from_db()
        self.assertEqual(len(self.msg.versions), 1)

    def test_non_empty_last_version_skips_revert(self):
        """末版本已有内容（失败前已产出部分输出）→ 保留失败版本，不回滚。"""
        ChatMessage.objects.filter(pk=self.msg.pk).update(
            versions=[{"content": "旧回答"}, {"content": "半成品"}], current_version=1
        )

        reverted, _ = _revert_regenerate_archive_sync(self.session.session_id, str(self.msg.id))

        self.assertIsNone(reverted)
        self.msg.refresh_from_db()
        self.assertEqual(len(self.msg.versions), 2)

    def test_message_not_found_returns_none(self):
        """message_id 不存在 → 严格定位不回退最新消息，返回 None。"""
        reverted, message_data = _revert_regenerate_archive_sync(self.session.session_id, "999999")

        self.assertIsNone(reverted)
        self.assertIsNone(message_data)


class RevertRegenerateArchivePublishTests(TestCase):
    """revert_regenerate_archive 编排：回滚成功 → 发布 MESSAGE_REGENERATE_REVERTED。"""

    def test_revert_success_publishes_event_with_payload(self):
        """回滚成功 → 发布事件（event type / payload / 频道），payload 通过 schema 校验。"""
        msg_fake = SimpleNamespace(id=7)
        message_data = {"content": "旧回答", "versions": [{"content": "旧回答"}]}
        with (
            mock.patch.object(
                sp_module, "_revert_regenerate_archive_sync", return_value=(msg_fake, message_data)
            ) as revert_mock,
            mock.patch(_REVERT_PUBLISH_TARGET, new=mock.AsyncMock()) as publish_mock,
        ):
            payload = asyncio.run(revert_regenerate_archive("sess-d6", "7"))

        revert_mock.assert_called_once_with("sess-d6", "7")
        self.assertEqual(
            payload,
            {"session_id": "sess-d6", "message_id": "7", "message": message_data},
        )
        publish_mock.assert_awaited_once()
        args, kwargs = publish_mock.call_args
        self.assertEqual(args[0], EventType.MESSAGE_REGENERATE_REVERTED)
        self.assertEqual(args[1], payload)
        self.assertEqual(kwargs, {"session_id": "sess-d6"})
        validate_payload(EventType.MESSAGE_REGENERATE_REVERTED, args[1])

    def test_revert_skip_publishes_nothing(self):
        """无可回滚空版本（sync 返回 None）→ 不发布。"""
        with (
            mock.patch.object(sp_module, "_revert_regenerate_archive_sync", return_value=(None, None)),
            mock.patch(_REVERT_PUBLISH_TARGET, new=mock.AsyncMock()) as publish_mock,
        ):
            payload = asyncio.run(revert_regenerate_archive("sess-d6", "7"))

        self.assertIsNone(payload)
        publish_mock.assert_not_awaited()

    def test_publish_failure_still_returns_payload(self):
        """广播失败仅 warning：回滚结果 payload 正常返回（不中断编排）。"""
        msg_fake = SimpleNamespace(id=7)
        with (
            mock.patch.object(
                sp_module, "_revert_regenerate_archive_sync", return_value=(msg_fake, {"content": "旧回答"})
            ),
            mock.patch(_REVERT_PUBLISH_TARGET, new=mock.AsyncMock(side_effect=RuntimeError("channel down"))),
        ):
            payload = asyncio.run(revert_regenerate_archive("sess-d6", "7"))

        self.assertEqual(payload["message_id"], "7")

    def test_serializer_failure_degrades_to_minimal_payload(self):
        """序列化失败 → 降级最小 payload（session_id + message_id）仍发布。"""
        msg_fake = SimpleNamespace(id=7)
        with (
            mock.patch.object(sp_module, "_revert_regenerate_archive_sync", return_value=(msg_fake, None)),
            mock.patch(_REVERT_PUBLISH_TARGET, new=mock.AsyncMock()) as publish_mock,
        ):
            payload = asyncio.run(revert_regenerate_archive("sess-d6", "7"))

        self.assertEqual(payload, {"session_id": "sess-d6", "message_id": "7"})
        publish_mock.assert_awaited_once()
