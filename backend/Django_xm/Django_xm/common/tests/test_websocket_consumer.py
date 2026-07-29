"""WebSocket consumer 单元测试。

覆盖 RealtimeSyncConsumer:
- connect / disconnect（订阅 user 频道、清理分组）
- handle_subscribe_session / handle_unsubscribe_session
- handle_subscribe_task / handle_unsubscribe_task
- handle_ping / handle_unknown
- broadcast_event（group_send → send_json 透传）
- handle_replay（历史回放）

mock 策略:
- mock authenticate_websocket_scope 返回 mock user
- 直接实例化 RealtimeSyncConsumer，注入 mock channel_layer / channel_name
- mock send_json 捕获输出
- 不依赖真实 Redis / Channels / DB

运行方式:
    cd backend/Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.common.tests.test_websocket_consumer --verbosity=2
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from Django_xm.apps.chat.consumers import RealtimeSyncConsumer
from Django_xm.common.realtime_events import _group_name


def _make_consumer(user_id=1):
    """构造一个用于测试的 RealtimeSyncConsumer 实例。

    - 设置 mock channel_layer / channel_name
    - 替换 send_json 为 AsyncMock 以捕获输出
    - 替换 accept 为 AsyncMock

    注：channels 4.x 中 scope 通过 ``__call__`` 在运行时设置，
    ``__init__`` 不接受 scope 参数。测试中显式赋值 ``consumer.scope``。
    同时显式初始化 ``connect()`` 中设置的属性（user_group/session_groups/
    task_groups），避免测试未调用 ``connect()`` 时报 AttributeError。
    """
    consumer = RealtimeSyncConsumer(
        scope={
            "type": "websocket",
            "query_string": b"token=fake-token",
        }
    )
    # channels 4.x: scope 通过 __call__ 在运行时设置，测试中显式赋值
    consumer.scope = {
        "type": "websocket",
        "query_string": b"token=fake-token",
    }
    consumer.channel_name = "test-channel-name"
    consumer.channel_layer = MagicMock()
    consumer.channel_layer.group_add = AsyncMock()
    consumer.channel_layer.group_discard = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.user = MagicMock()
    consumer.user.id = user_id
    consumer.user.is_authenticated = True
    # 显式初始化 connect() 中设置的属性，避免测试未调用 connect() 时报 AttributeError
    consumer.user_id = user_id
    consumer.user_group = f"user_{user_id}"
    consumer.session_groups = set()
    consumer.task_groups = set()
    return consumer


class ConnectTests(unittest.IsolatedAsyncioTestCase):
    """connect 方法测试。"""

    @patch("Django_xm.apps.chat.consumers.authenticate_websocket_scope")
    async def test_connect_subscribes_user_group(self, mock_auth):
        """connect 后订阅 user 频道并发送 connected 消息。"""
        mock_user = MagicMock()
        mock_user.id = 42
        mock_user.is_authenticated = True
        mock_auth.return_value = mock_user

        consumer = RealtimeSyncConsumer(
            scope={
                "type": "websocket",
                "query_string": b"token=fake",
            }
        )
        # channels 4.x: scope 通过 __call__ 在运行时设置，测试中显式赋值
        consumer.scope = {
            "type": "websocket",
            "query_string": b"token=fake",
        }
        consumer.channel_name = "test-channel"
        mock_layer = MagicMock()
        mock_layer.group_add = AsyncMock()
        consumer.channel_layer = mock_layer
        consumer.send_json = AsyncMock()
        consumer.accept = AsyncMock()

        await consumer.connect()

        # 订阅 user_42 分组
        mock_layer.group_add.assert_called_once_with("user_42", "test-channel")
        # accept 被调用
        consumer.accept.assert_called_once()
        # 发送 connected 消息
        consumer.send_json.assert_called_once()
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "connected")
        self.assertEqual(sent["channel"], "user_42")
        # user_group / session_groups 属性已初始化
        self.assertEqual(consumer.user_group, "user_42")
        self.assertEqual(consumer.session_groups, set())
        self.assertEqual(consumer.task_groups, set())

    @patch("Django_xm.apps.chat.consumers.authenticate_websocket_scope")
    async def test_connect_rejected_when_not_authenticated(self, mock_auth):
        """未认证时关闭连接（close code=4001）。"""
        mock_user = MagicMock()
        mock_user.is_authenticated = False
        mock_auth.return_value = mock_user

        consumer = RealtimeSyncConsumer(
            scope={
                "type": "websocket",
                "query_string": b"",
            }
        )
        # channels 4.x: scope 通过 __call__ 在运行时设置，测试中显式赋值
        consumer.scope = {
            "type": "websocket",
            "query_string": b"",
        }
        consumer.close = AsyncMock()
        consumer.send_json = AsyncMock()
        consumer.accept = AsyncMock()

        await consumer.connect()

        consumer.close.assert_called_once_with(code=4001)
        consumer.accept.assert_not_called()
        consumer.send_json.assert_not_called()

    @patch("Django_xm.apps.chat.consumers.authenticate_websocket_scope")
    async def test_connect_rejected_when_user_none(self, mock_auth):
        """authenticate 返回 None 时关闭连接。"""
        mock_auth.return_value = None

        consumer = RealtimeSyncConsumer(
            scope={
                "type": "websocket",
                "query_string": b"",
            }
        )
        # channels 4.x: scope 通过 __call__ 在运行时设置，测试中显式赋值
        consumer.scope = {
            "type": "websocket",
            "query_string": b"",
        }
        consumer.close = AsyncMock()
        consumer.accept = AsyncMock()

        await consumer.connect()

        consumer.close.assert_called_once_with(code=4001)


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    """disconnect 方法测试。"""

    async def test_disconnect_cleans_user_group(self):
        """disconnect 离开 user 分组。"""
        consumer = _make_consumer(user_id=1)
        consumer.user_group = "user_1"
        consumer.session_groups = set()
        consumer.task_groups = set()

        await consumer.disconnect(1000)

        consumer.channel_layer.group_discard.assert_called_once_with("user_1", "test-channel-name")

    async def test_disconnect_cleans_session_groups(self):
        """disconnect 离开所有已订阅的 session 分组。"""
        consumer = _make_consumer(user_id=1)
        consumer.user_group = "user_1"
        consumer.session_groups = {"session_a", "session_b"}
        consumer.task_groups = set()

        await consumer.disconnect(1000)

        # 3 次 group_discard：1 个 user + 2 个 session
        self.assertEqual(consumer.channel_layer.group_discard.call_count, 3)
        discarded_groups = [call.args[0] for call in consumer.channel_layer.group_discard.call_args_list]
        self.assertIn("user_1", discarded_groups)
        self.assertIn("session_a", discarded_groups)
        self.assertIn("session_b", discarded_groups)

    async def test_disconnect_cleans_task_groups(self):
        """disconnect 离开所有已订阅的 task 分组。"""
        consumer = _make_consumer(user_id=1)
        consumer.user_group = "user_1"
        consumer.session_groups = set()
        consumer.task_groups = {"task_a"}

        await consumer.disconnect(1000)

        self.assertEqual(consumer.channel_layer.group_discard.call_count, 2)
        discarded_groups = [call.args[0] for call in consumer.channel_layer.group_discard.call_args_list]
        self.assertIn("task_a", discarded_groups)

    async def test_disconnect_without_user_group_no_raise(self):
        """disconnect 时若未设置 user_group（异常情况）不抛异常。"""
        consumer = _make_consumer(user_id=1)
        # 不设置 user_group 属性，模拟 connect 失败后的 disconnect

        await consumer.disconnect(1000)
        # 不抛异常即通过


class SubscribeSessionTests(unittest.IsolatedAsyncioTestCase):
    """handle_subscribe_session / handle_unsubscribe_session 测试。"""

    async def test_subscribe_session_success(self):
        """订阅 session 频道成功，加入分组并响应 subscribed。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=True)

        await consumer.handle_subscribe_session({"session_id": "session-1"})

        group = _group_name("session", "session-1")
        consumer.channel_layer.group_add.assert_called_once_with(group, "test-channel-name")
        self.assertIn(group, consumer.session_groups)
        # 响应 subscribed
        consumer.send_json.assert_called()
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "subscribed")
        self.assertEqual(sent["channel"], group)

    async def test_subscribe_session_missing_id(self):
        """缺少 session_id 参数响应 error。"""
        consumer = _make_consumer(user_id=1)

        await consumer.handle_subscribe_session({})

        consumer.send_json.assert_called_once()
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "error")
        self.assertEqual(sent["code"], "40002")
        consumer.channel_layer.group_add.assert_not_called()

    async def test_subscribe_session_no_permission(self):
        """无权限订阅响应 error（code=40401）。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=False)

        await consumer.handle_subscribe_session({"session_id": "session-1"})

        consumer.send_json.assert_called_once()
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "error")
        self.assertEqual(sent["code"], "40401")
        consumer.channel_layer.group_add.assert_not_called()

    async def test_subscribe_session_idempotent(self):
        """重复订阅同一 session 不重复加入分组。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=True)

        await consumer.handle_subscribe_session({"session_id": "session-1"})
        await consumer.handle_subscribe_session({"session_id": "session-1"})

        # 仅加入一次
        self.assertEqual(consumer.channel_layer.group_add.call_count, 1)

    async def test_unsubscribe_session_success(self):
        """取消订阅 session 频道。"""
        consumer = _make_consumer(user_id=1)
        group = _group_name("session", "session-1")
        consumer.session_groups.add(group)

        await consumer.handle_unsubscribe_session({"session_id": "session-1"})

        consumer.channel_layer.group_discard.assert_called_once_with(group, "test-channel-name")
        self.assertNotIn(group, consumer.session_groups)
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "unsubscribed")

    async def test_unsubscribe_session_missing_id(self):
        """取消订阅缺少 session_id 响应 error。"""
        consumer = _make_consumer(user_id=1)

        await consumer.handle_unsubscribe_session({})

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "error")
        self.assertEqual(sent["code"], "40002")

    async def test_unsubscribe_session_not_subscribed(self):
        """取消订阅未订阅的 session 不调用 group_discard。"""
        consumer = _make_consumer(user_id=1)

        await consumer.handle_unsubscribe_session({"session_id": "session-1"})

        consumer.channel_layer.group_discard.assert_not_called()
        # 仍响应 unsubscribed
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "unsubscribed")


class SubscribeTaskTests(unittest.IsolatedAsyncioTestCase):
    """handle_subscribe_task / handle_unsubscribe_task 测试。"""

    async def test_subscribe_task_success(self):
        """订阅 task 频道成功。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_task = AsyncMock(return_value=True)

        await consumer.handle_subscribe_task({"task_id": "task-1"})

        group = _group_name("task", "task-1")
        consumer.channel_layer.group_add.assert_called_once_with(group, "test-channel-name")
        self.assertIn(group, consumer.task_groups)
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "subscribed")

    async def test_subscribe_task_missing_id(self):
        """缺少 task_id 响应 error。"""
        consumer = _make_consumer(user_id=1)

        await consumer.handle_subscribe_task({})

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "error")
        self.assertEqual(sent["code"], "40002")

    async def test_subscribe_task_no_permission(self):
        """无权限订阅 task 响应 error。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_task = AsyncMock(return_value=False)

        await consumer.handle_subscribe_task({"task_id": "task-1"})

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["code"], "40401")

    async def test_unsubscribe_task_success(self):
        """取消订阅 task 频道。"""
        consumer = _make_consumer(user_id=1)
        group = _group_name("task", "task-1")
        consumer.task_groups.add(group)

        await consumer.handle_unsubscribe_task({"task_id": "task-1"})

        consumer.channel_layer.group_discard.assert_called_once_with(group, "test-channel-name")
        self.assertNotIn(group, consumer.task_groups)


class PingTests(unittest.IsolatedAsyncioTestCase):
    """handle_ping 测试。"""

    async def test_ping_returns_pong(self):
        """ping 动作响应 pong。"""
        consumer = _make_consumer(user_id=1)

        await consumer.handle_ping({})

        consumer.send_json.assert_called_once()
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "pong")
        self.assertIn("timestamp", sent)


class UnknownActionTests(unittest.IsolatedAsyncioTestCase):
    """handle_unknown / receive_json 测试。"""

    async def test_unknown_action_returns_error(self):
        """未知 action 响应 error（code=40001）。"""
        consumer = _make_consumer(user_id=1)

        await consumer.handle_unknown({})

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "error")
        self.assertEqual(sent["code"], "40001")

    async def test_receive_json_routes_to_handler(self):
        """receive_json 根据 action 路由到对应 handler。"""
        consumer = _make_consumer(user_id=1)
        consumer.handle_ping = AsyncMock()

        await consumer.receive_json({"action": "ping", "payload": {}})

        consumer.handle_ping.assert_called_once_with({})

    async def test_receive_json_unknown_action_routes_to_unknown(self):
        """未知 action 路由到 handle_unknown。"""
        consumer = _make_consumer(user_id=1)
        consumer.handle_unknown = AsyncMock()

        await consumer.receive_json({"action": "not_exist", "payload": {}})

        consumer.handle_unknown.assert_called_once_with({})

    async def test_receive_json_handler_exception_returns_error(self):
        """handler 抛异常时响应 error（code=50001）。"""
        consumer = _make_consumer(user_id=1)
        consumer.handle_ping = AsyncMock(side_effect=RuntimeError("boom"))

        await consumer.receive_json({"action": "ping", "payload": {}})

        # 应该发送 error 消息而非抛出
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "error")
        self.assertEqual(sent["code"], "50001")


class BroadcastEventTests(unittest.IsolatedAsyncioTestCase):
    """broadcast_event 测试（channel layer → 客户端透传）。"""

    async def test_broadcast_event_with_wrapped_event(self):
        """group_send 事件包含 event 字段 → 透传 event 给客户端。"""
        consumer = _make_consumer(user_id=1)

        wrapped_event = {
            "type": "session_created",
            "seq": 5,
            "timestamp": 1234567890.0,
            "payload": {"session_id": "s-1"},
        }
        await consumer.broadcast_event({"type": "broadcast_event", "event": wrapped_event})

        consumer.send_json.assert_called_once_with(wrapped_event)

    async def test_broadcast_event_without_wrapper(self):
        """group_send 事件不包含 event 字段 → 兼容透传（去除 type 字段）。"""
        consumer = _make_consumer(user_id=1)

        raw_event = {
            "type": "broadcast_event",
            "message": "hello",
            "seq": 1,
        }
        await consumer.broadcast_event(raw_event)

        consumer.send_json.assert_called_once()
        sent = consumer.send_json.call_args.args[0]
        self.assertNotIn("type", sent)
        self.assertEqual(sent["message"], "hello")
        self.assertEqual(sent["seq"], 1)


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    """handle_replay 历史回放测试。"""

    @patch("Django_xm.apps.chat.consumers.get_event_history")
    async def test_replay_session_returns_history(self, mock_history):
        """replay session 频道返回历史事件列表。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=True)
        mock_history.return_value = [
            {"type": "message_added", "seq": 2, "payload": {}},
            {"type": "message_added", "seq": 3, "payload": {}},
        ]

        await consumer.handle_replay(
            {
                "channel_type": "session",
                "channel_id": "session-1",
                "last_seq": 1,
            }
        )

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "replay")
        self.assertEqual(sent["channel_type"], "session")
        self.assertEqual(sent["channel_id"], "session-1")
        self.assertEqual(sent["count"], 2)
        self.assertEqual(len(sent["events"]), 2)

    @patch("Django_xm.apps.chat.consumers.get_event_history")
    async def test_replay_task_returns_history(self, mock_history):
        """replay task 频道返回历史事件。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_task = AsyncMock(return_value=True)
        mock_history.return_value = []

        await consumer.handle_replay(
            {
                "channel_type": "task",
                "channel_id": "task-1",
                "last_seq": 0,
            }
        )

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "replay")
        self.assertEqual(sent["count"], 0)

    async def test_replay_invalid_channel_type(self):
        """非法 channel_type 响应 error。"""
        consumer = _make_consumer(user_id=1)

        await consumer.handle_replay(
            {
                "channel_type": "invalid",
                "channel_id": "x",
            }
        )

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "error")
        self.assertEqual(sent["code"], "40003")

    async def test_replay_missing_channel_id(self):
        """缺少 channel_id 响应 error。"""
        consumer = _make_consumer(user_id=1)

        await consumer.handle_replay(
            {
                "channel_type": "session",
                "channel_id": "",
            }
        )

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["code"], "40003")

    async def test_replay_session_no_permission(self):
        """replay session 无权限响应 error。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=False)

        await consumer.handle_replay(
            {
                "channel_type": "session",
                "channel_id": "session-1",
            }
        )

        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["code"], "40401")


class SubscribeWithReplayTests(unittest.IsolatedAsyncioTestCase):
    """订阅时携带 last_seq 自动回放历史事件。"""

    @patch("Django_xm.apps.chat.consumers.get_event_history")
    async def test_subscribe_session_with_last_seq_replays(self, mock_history):
        """handle_subscribe_session 携带 last_seq → 回放历史事件。

        历史事件通过 ``_send_replay_chunked`` 统一包装为 ``type='replay'`` 消息发送，
        与 ``handle_replay`` 行为一致（避免 500 条事件超过 WebSocket 1MB payload 限制）。
        前端 ``useRealtimeSync`` barrier 机制负责解包 ``events`` 数组并按 seq 排序处理。
        """
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=True)
        mock_history.return_value = [
            {"type": "message_added", "seq": 5, "payload": {}},
        ]

        await consumer.handle_subscribe_session(
            {
                "session_id": "session-1",
                "last_seq": 4,
            }
        )

        # 第一次 send_json 是 subscribed 响应，第二次是 replay 包装的历史事件
        self.assertEqual(consumer.send_json.call_count, 2)
        # 第二次发送的是 replay 包装消息
        second_call = consumer.send_json.call_args_list[1].args[0]
        self.assertEqual(second_call["type"], "replay")
        self.assertEqual(second_call["channel_type"], "session")
        self.assertEqual(second_call["channel_id"], "session-1")
        self.assertEqual(second_call["count"], 1)
        self.assertEqual(len(second_call["events"]), 1)
        # 原始历史事件在 events 数组中
        self.assertEqual(second_call["events"][0]["type"], "message_added")
        self.assertEqual(second_call["events"][0]["seq"], 5)

    @patch("Django_xm.apps.chat.consumers.get_event_history")
    async def test_subscribe_session_no_last_seq_no_replay(self, mock_history):
        """handle_subscribe_session 不携带 last_seq → 不回放。"""
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=True)

        await consumer.handle_subscribe_session({"session_id": "session-1"})

        # 仅 subscribed 响应，无历史回放
        self.assertEqual(consumer.send_json.call_count, 1)
        mock_history.assert_not_called()

    @patch("Django_xm.apps.chat.consumers.get_event_history")
    async def test_subscribe_task_with_last_seq_replays(self, mock_history):
        """handle_subscribe_task 携带 last_seq → 回放历史事件。

        与 ``handle_subscribe_session`` 一致，历史事件通过 ``_send_replay_chunked``
        包装为 ``type='replay'`` 消息发送。
        """
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_task = AsyncMock(return_value=True)
        mock_history.return_value = [
            {"type": "tool_call_running", "seq": 3, "payload": {}},
        ]

        await consumer.handle_subscribe_task(
            {
                "task_id": "task-1",
                "last_seq": 2,
            }
        )

        # 第二次发送 replay 包装消息
        self.assertEqual(consumer.send_json.call_count, 2)
        second_call = consumer.send_json.call_args_list[1].args[0]
        self.assertEqual(second_call["type"], "replay")
        self.assertEqual(second_call["channel_type"], "task")
        self.assertEqual(second_call["channel_id"], "task-1")
        self.assertEqual(second_call["count"], 1)
        # 原始历史事件在 events 数组中
        self.assertEqual(second_call["events"][0]["type"], "tool_call_running")
        self.assertEqual(second_call["events"][0]["seq"], 3)


if __name__ == "__main__":
    unittest.main()
