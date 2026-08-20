"""user 频道历史事件幽灵会话过滤测试（C3第②批：依赖方向债务清偿）。

背景（迁移）：
- filter_ghost_session_created 原位于 common/realtime_events.py（私有 _filter_ghost_session_created），
  因依赖 ChatSession 模型违反「common 基础层禁止依赖 apps 业务层」契约；
- 迁移至 apps/chat/services/session_history.py 并公共化，
  common 侧经 set_user_history_filter 注册点由 chat app ready() 注入（依赖反转）。

覆盖：
- 幽灵会话（数据库不存在 / 已删除）的 session_created 事件被跳过
- 存在会话的 session_created 事件 payload 被数据库最新字段覆盖
  （title / mode / message_count / selected_knowledge_bases / updated_at）
- 非 session_created 事件原样保留
- 无 session_created 事件时快速返回原列表
- DB 查询异常时保持原样回放（不影响实时同步可用性）
- get_event_history 注册注入链路：set_user_history_filter 注册后，
  user 频道回放经过滤器，session/task 频道不经过滤器
- 注册接线：ChatConfig.ready() 将 filter_ghost_session_created 注入注册点

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_session_history --settings=Django_xm.settings.test
"""

import json
import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.apps.chat.models import ChatSession
from Django_xm.apps.chat.services.session_history import filter_ghost_session_created
from Django_xm.common import realtime_events


def _created_event(seq, session_id, title="old-title"):
    """构造一条 session_created 历史事件（snake_case 网络键名）。"""
    return {
        "type": "session_created",
        "seq": seq,
        "timestamp": 1234567890.0,
        "payload": {"session_id": session_id, "title": title, "mode": "chat"},
    }


class FilterGhostSessionCreatedTests(TestCase):
    """filter_ghost_session_created 纯过滤行为。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ghostf", password="pw123456")
        self.session = ChatSession.objects.create(
            user=self.user,
            title="db-title",
            session_id="sess-live",
            mode="chat",
            selected_knowledge_bases=["kb-1"],
        )

    def test_ghost_session_event_is_skipped(self):
        """数据库不存在的会话：session_created 事件被跳过（幽灵过滤）。"""
        events = [
            _created_event(1, "sess-live"),
            _created_event(2, "sess-ghost"),
        ]
        filtered = filter_ghost_session_created(events, self.user.id)
        self.assertEqual([e["payload"]["session_id"] for e in filtered], ["sess-live"])

    def test_live_session_payload_overridden_by_db_fields(self):
        """存在会话：payload 权威字段被数据库最新记录覆盖。"""
        events = [_created_event(1, "sess-live", title="stale-title")]
        (filtered,) = filter_ghost_session_created(events, self.user.id)
        payload = filtered["payload"]
        self.assertEqual(payload["title"], "db-title")
        self.assertEqual(payload["mode"], "chat")
        self.assertEqual(payload["message_count"], 0)
        self.assertEqual(payload["selected_knowledge_bases"], ["kb-1"])
        self.assertIn("updated_at", payload)

    def test_non_session_created_events_pass_through(self):
        """非 session_created 事件原样保留（同对象引用）。"""
        other = {"type": "message_added", "seq": 3, "payload": {"session_id": "sess-ghost"}}
        events = [_created_event(1, "sess-ghost"), other]
        filtered = filter_ghost_session_created(events, self.user.id)
        self.assertEqual(filtered, [other])

    def test_no_session_created_returns_unchanged(self):
        """无 session_created 事件：快速返回原列表（不查库）。"""
        events = [{"type": "message_added", "seq": 1, "payload": {}}]
        with mock.patch("Django_xm.apps.chat.models.ChatSession.objects") as qs:
            result = filter_ghost_session_created(events, self.user.id)
        self.assertIs(result, events)
        qs.filter.assert_not_called()

    def test_db_failure_keeps_original_events(self):
        """DB 查询异常：保持原样回放（不影响实时同步可用性）。"""
        events = [_created_event(1, "sess-live")]
        with mock.patch("Django_xm.apps.chat.models.ChatSession.objects") as qs:
            qs.filter.side_effect = Exception("db down")
            result = filter_ghost_session_created(events, self.user.id)
        self.assertIs(result, events)


class UserHistoryFilterInjectionTests(TestCase):
    """get_event_history 注册注入链路（依赖反转）。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ghosti", password="pw123456")
        # 每个用例独立控制注册点状态，不依赖执行顺序
        realtime_events.set_user_history_filter(None)

    def tearDown(self):
        # 还原注册点为 chat app ready() 的生产注册，避免污染其它用例
        realtime_events.set_user_history_filter(filter_ghost_session_created)

    def _patch_redis_history(self, events):
        """mock Redis lrange 返回预构造事件列表。"""
        raw = [json.dumps(e).encode("utf-8") for e in events]
        fake_client = mock.Mock()
        fake_client.lrange.return_value = raw
        return mock.patch.object(realtime_events, "get_redis_client", return_value=fake_client)

    def test_user_channel_replays_through_registered_filter(self):
        """user 频道：注册过滤器后，幽灵 session_created 不回放、存活会话字段被覆盖。"""
        ChatSession.objects.create(user=self.user, title="live", session_id="sess-live")
        realtime_events.set_user_history_filter(filter_ghost_session_created)
        events = [_created_event(1, "sess-live"), _created_event(2, "sess-ghost")]

        with self._patch_redis_history(events):
            replayed = realtime_events.get_event_history("user", self.user.id)

        self.assertEqual([e["payload"]["session_id"] for e in replayed], ["sess-live"])
        self.assertEqual(replayed[0]["payload"]["title"], "live")

    def test_session_channel_bypasses_user_filter(self):
        """session / task 频道：不经 user 过滤器（即使已注册）。"""
        realtime_events.set_user_history_filter(
            lambda events, user_id: []  # 若被误调用则返回空，导致断言失败
        )
        events = [_created_event(1, "sess-x")]

        with self._patch_redis_history(events):
            replayed = realtime_events.get_event_history("session", "sess-x")

        self.assertEqual(len(replayed), 1)

    def test_unregistered_filter_keeps_original_replay(self):
        """未注册过滤器：user 频道原样回放（common 层独立可用）。"""
        events = [_created_event(1, "sess-ghost")]

        with self._patch_redis_history(events):
            replayed = realtime_events.get_event_history("user", self.user.id)

        self.assertEqual(len(replayed), 1)

    def test_chat_ready_registers_filter(self):
        """注册接线：ChatConfig.ready() 将 filter_ghost_session_created 注入注册点。"""
        from django.apps import apps as django_apps

        chat_config = django_apps.get_app_config("chat")
        chat_config.ready()  # 重新触发注册（幂等）

        self.assertIs(realtime_events._user_history_filter, filter_ghost_session_created)
