"""WebSocket consumer 单元测试补充。

本模块作为 ``Django_xm.common.tests.test_websocket_consumer`` 的补充，
专门覆盖以下用例（与 test_websocket_consumer.py 不重复）：

- SubTask 8.8 事件广播：
  - 收到 session:{session_id} 频道消息时转发给客户端
  - 收到 task:{task_id} 频道消息时转发给客户端
- SubTask 8.9 历史回放：
  - last_seq 参数请求历史事件回放
  - 回放完成后恢复正常订阅（session_groups 仍包含目标 group）

mock 策略:
- 直接实例化 RealtimeSyncConsumer，注入 mock channel_layer / channel_name
- mock send_json 捕获输出
- mock get_event_history 隔离 Redis 依赖
- 不依赖真实 Redis / Channels / DB

运行方式:
    cd backend/Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.chat.tests.test_consumers --verbosity=2
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from Django_xm.apps.chat.consumers import RealtimeSyncConsumer
from Django_xm.common.realtime_events import _group_name


def _make_consumer(user_id=1):
    """构造一个用于测试的 RealtimeSyncConsumer 实例。

    与 ``test_websocket_consumer.py`` 中的同名辅助函数保持一致，
    使本测试模块可独立运行而不依赖其他测试文件。

    注：channels 4.x 中 scope 通过 ``__call__`` 在运行时设置，
    ``__init__`` 不接受 scope 参数。测试中显式赋值 ``consumer.scope``。
    """
    consumer = RealtimeSyncConsumer(scope={
        "type": "websocket",
        "query_string": b"token=fake-token",
    })
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
    consumer.user_id = user_id
    consumer.user_group = f"user_{user_id}"
    consumer.session_groups = set()
    consumer.task_groups = set()
    return consumer


class BroadcastFromSessionChannelTests(unittest.IsolatedAsyncioTestCase):
    """SubTask 8.8：收到 session:{session_id} 频道消息时转发给客户端。"""

    async def test_broadcast_event_from_session_channel(self):
        """session 频道 group_send 事件被 broadcast_event 透传给客户端。

        场景：调用方通过 publish_event 发布事件到 session:{session_id} 频道，
        channel layer 调用 consumer.broadcast_event（type=broadcast_event），
        consumer 将包装的 event 透传给客户端 WebSocket。
        """
        consumer = _make_consumer(user_id=1)

        # 模拟 session 频道 group_send 的事件
        wrapped_event = {
            "type": "session_created",
            "seq": 5,
            "timestamp": 1234567890.0,
            "payload": {"session_id": "session-1"},
        }
        # Channels group_send 调用时携带的 message 包含 type=broadcast_event + event 字段
        channel_message = {
            "type": "broadcast_event",
            "event": wrapped_event,
        }

        await consumer.broadcast_event(channel_message)

        # 透传给客户端的是 wrapped_event（剥离外层 broadcast_event 包装）
        consumer.send_json.assert_called_once_with(wrapped_event)

    async def test_broadcast_event_session_with_tool_call_payload(self):
        """session 频道工具调用事件透传（覆盖聊天场景工具调用推送）。"""
        consumer = _make_consumer(user_id=1)

        wrapped_event = {
            "type": "tool_call_running",
            "seq": 10,
            "timestamp": 1234567890.0,
            "payload": {
                "tool_call_id": "tc-1",
                "tool_name": "shell_exec",
                "source": "chat",
                "source_id": "session-1",
            },
        }

        await consumer.broadcast_event({
            "type": "broadcast_event",
            "event": wrapped_event,
        })

        consumer.send_json.assert_called_once_with(wrapped_event)


class BroadcastFromTaskChannelTests(unittest.IsolatedAsyncioTestCase):
    """SubTask 8.8：收到 task:{task_id} 频道消息时转发给客户端。

    独立深度研究场景：事件发布到 task:{task_id} 频道，
    consumer 通过 broadcast_event 透传给客户端。
    """

    async def test_broadcast_event_from_task_channel(self):
        """task 频道 group_send 事件被 broadcast_event 透传给客户端。

        场景：调用方通过 publish_event(task_id='task-1', ...) 发布事件，
        channel layer 调用 consumer.broadcast_event（type=broadcast_event），
        consumer 将包装的 event 透传给客户端 WebSocket。
        """
        consumer = _make_consumer(user_id=1)

        # 模拟 task 频道 group_send 的事件
        wrapped_event = {
            "type": "tool_call_running",
            "seq": 3,
            "timestamp": 1234567890.0,
            "payload": {
                "tool_call_id": "tc-1",
                "tool_name": "deep_search",
                "source": "deep_research",
                "source_id": "task-1",
            },
        }
        channel_message = {
            "type": "broadcast_event",
            "event": wrapped_event,
        }

        await consumer.broadcast_event(channel_message)

        # 透传给客户端的是 wrapped_event（与 session 频道一致）
        consumer.send_json.assert_called_once_with(wrapped_event)

    async def test_broadcast_event_task_approval_event(self):
        """task 频道审批事件透传（覆盖深度研究审批推送）。"""
        consumer = _make_consumer(user_id=1)

        wrapped_event = {
            "type": "approval_pending",
            "seq": 7,
            "timestamp": 1234567890.0,
            "payload": {
                "interrupt_id": "intr-1",
                "tool_call_id": "tc-1",
                "source": "deep_research",
                "source_id": "task-1",
                "state": "pending",
            },
        }

        await consumer.broadcast_event({
            "type": "broadcast_event",
            "event": wrapped_event,
        })

        consumer.send_json.assert_called_once_with(wrapped_event)


class ReplayAndResumeSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    """SubTask 8.9：历史回放 + 回放完成后恢复正常订阅。

    场景：客户端订阅 session/task 频道时携带 last_seq，consumer 应：
    1. 加入对应 group
    2. 响应 subscribed 消息
    3. 回放 seq > last_seq 的历史事件（每个事件单独 send_json）
    4. 回放完成后保持订阅状态（session_groups/task_groups 仍包含目标 group）
       → 后续 group_send 的新事件能正常透传
    """

    @patch('Django_xm.apps.chat.consumers.get_event_history')
    async def test_replay_session_then_resume_subscription(self, mock_history):
        """SubTask 8.9：session 订阅 + 历史回放 + 回放完成后订阅仍有效。

        - 订阅 session-1 携带 last_seq=4
        - 回放 seq=5 的历史事件（通过 ``_send_replay_chunked`` 包装为 ``type='replay'``）
        - 验证 session_groups 仍包含 session_session-1 分组
        - 模拟后续 group_send 的新事件（seq=6），broadcast_event 能透传

        历史事件通过 ``_send_replay_chunked`` 统一包装为 ``type='replay'`` 消息发送
        （避免 500 条事件超过 WebSocket 1MB payload 限制）。前端 ``useRealtimeSync``
        barrier 机制负责解包 ``events`` 数组并按 seq 排序处理。
        """
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=True)
        mock_history.return_value = [
            {"type": "message_added", "seq": 5, "payload": {"content": "hi"}},
        ]

        # 1. 订阅并触发回放
        await consumer.handle_subscribe_session({
            "session_id": "session-1",
            "last_seq": 4,
        })

        # 2. send_json 调用次数：subscribed + 1 个 replay 包装 = 2 次
        self.assertEqual(consumer.send_json.call_count, 2)

        # 3. 第一次：subscribed 响应
        subscribed_msg = consumer.send_json.call_args_list[0].args[0]
        self.assertEqual(subscribed_msg["type"], "subscribed")

        # 4. 第二次：replay 包装消息（历史事件在 events 数组中）
        replay_msg = consumer.send_json.call_args_list[1].args[0]
        self.assertEqual(replay_msg["type"], "replay")
        self.assertEqual(replay_msg["channel_type"], "session")
        self.assertEqual(replay_msg["channel_id"], "session-1")
        self.assertEqual(replay_msg["count"], 1)
        self.assertEqual(len(replay_msg["events"]), 1)
        # 原始历史事件在 events 数组中
        self.assertEqual(replay_msg["events"][0]["type"], "message_added")
        self.assertEqual(replay_msg["events"][0]["seq"], 5)

        # 5. 回放完成后 session_groups 仍包含目标 group（恢复正常订阅）
        expected_group = _group_name("session", "session-1")
        self.assertIn(expected_group, consumer.session_groups)

        # 6. 模拟后续 group_send 的新事件，broadcast_event 应正常透传
        consumer.send_json.reset_mock()
        new_event = {
            "type": "message_added",
            "seq": 6,
            "timestamp": 1234567890.0,
            "payload": {"content": "new message after replay"},
        }
        await consumer.broadcast_event({
            "type": "broadcast_event",
            "event": new_event,
        })
        consumer.send_json.assert_called_once_with(new_event)

    @patch('Django_xm.apps.chat.consumers.get_event_history')
    async def test_replay_task_then_resume_subscription(self, mock_history):
        """SubTask 8.9：task 订阅 + 历史回放 + 回放完成后订阅仍有效。

        - 订阅 task-1 携带 last_seq=2
        - 回放 seq=3 的历史事件（通过 ``_send_replay_chunked`` 包装为 ``type='replay'``）
        - 验证 task_groups 仍包含 task_task-1 分组

        与 ``handle_subscribe_session`` 一致，历史事件通过 ``_send_replay_chunked``
        包装为 ``type='replay'`` 消息发送。
        """
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_task = AsyncMock(return_value=True)
        mock_history.return_value = [
            {"type": "tool_call_running", "seq": 3, "payload": {}},
        ]

        # 1. 订阅并触发回放
        await consumer.handle_subscribe_task({
            "task_id": "task-1",
            "last_seq": 2,
        })

        # 2. send_json 调用次数：subscribed + 1 个 replay 包装 = 2 次
        self.assertEqual(consumer.send_json.call_count, 2)

        # 3. 第二次：replay 包装消息（历史事件在 events 数组中）
        replay_msg = consumer.send_json.call_args_list[1].args[0]
        self.assertEqual(replay_msg["type"], "replay")
        self.assertEqual(replay_msg["channel_type"], "task")
        self.assertEqual(replay_msg["channel_id"], "task-1")
        self.assertEqual(replay_msg["count"], 1)
        # 原始历史事件在 events 数组中
        self.assertEqual(replay_msg["events"][0]["type"], "tool_call_running")
        self.assertEqual(replay_msg["events"][0]["seq"], 3)

        # 4. 回放完成后 task_groups 仍包含目标 group（恢复正常订阅）
        expected_group = _group_name("task", "task-1")
        self.assertIn(expected_group, consumer.task_groups)

        # 5. 模拟后续 group_send 的新事件，broadcast_event 应正常透传
        consumer.send_json.reset_mock()
        new_event = {
            "type": "tool_call_completed",
            "seq": 4,
            "timestamp": 1234567890.0,
            "payload": {"tool_call_id": "tc-1"},
        }
        await consumer.broadcast_event({
            "type": "broadcast_event",
            "event": new_event,
        })
        consumer.send_json.assert_called_once_with(new_event)

    @patch('Django_xm.apps.chat.consumers.get_event_history')
    async def test_replay_empty_history_subscription_still_active(self, mock_history):
        """SubTask 8.9：last_seq 等于当前最大 seq 时回放空列表，订阅仍有效。

        边界场景：客户端 last_seq 已是最新，回放 0 条事件，
        但订阅状态仍正常（session_groups 包含目标 group）。

        ``_send_replay_chunked`` 对空历史仍发送 ``type='replay'`` 消息
        （``count=0, events=[]``），前端 barrier 机制据此完成 replay 阶段。
        """
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=True)
        mock_history.return_value = []  # 无新事件

        await consumer.handle_subscribe_session({
            "session_id": "session-1",
            "last_seq": 100,
        })

        # subscribed + replay(count=0) = 2 次
        self.assertEqual(consumer.send_json.call_count, 2)
        first = consumer.send_json.call_args_list[0].args[0]
        self.assertEqual(first["type"], "subscribed")
        second = consumer.send_json.call_args_list[1].args[0]
        self.assertEqual(second["type"], "replay")
        self.assertEqual(second["count"], 0)
        self.assertEqual(second["events"], [])

        # 订阅状态仍正常
        expected_group = _group_name("session", "session-1")
        self.assertIn(expected_group, consumer.session_groups)

    @patch('Django_xm.apps.chat.consumers.get_event_history')
    async def test_replay_history_failure_does_not_break_subscription(self, mock_history):
        """SubTask 8.9：回放过程异常时订阅仍有效（不破坏订阅状态）。

        consumer.handle_subscribe_session 在回放失败时仅记录 warning，
        不影响订阅状态（session_groups 仍包含目标 group）。
        """
        consumer = _make_consumer(user_id=1)
        consumer._user_owns_session = AsyncMock(return_value=True)
        mock_history.side_effect = RuntimeError("redis error")

        # 不应抛出异常
        await consumer.handle_subscribe_session({
            "session_id": "session-1",
            "last_seq": 4,
        })

        # 仅 subscribed 响应，无历史事件（回放失败）
        self.assertEqual(consumer.send_json.call_count, 1)
        sent = consumer.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "subscribed")

        # 订阅状态仍正常（回放失败不影响订阅）
        expected_group = _group_name("session", "session-1")
        self.assertIn(expected_group, consumer.session_groups)


if __name__ == '__main__':
    unittest.main()
