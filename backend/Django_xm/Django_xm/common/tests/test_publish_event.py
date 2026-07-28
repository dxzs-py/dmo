"""publish_event 单元测试。

覆盖:
- 频道路由（session/task/user 单频道、双频道、三者为 None）
- payload 校验失败时不发布且抛出 PayloadValidationError（Task 1 修复后行为）
- 错误处理（Redis 连接失败、group_send 失败）

mock 策略:
- mock Django_xm.common.realtime_events._get_redis_client 返回 mock Redis 客户端
- mock Django_xm.common.realtime_events.get_channel_layer 返回 mock channel layer
- mock Django_xm.common.realtime_events._atomic_publish_event 返回固定 seq
- 不依赖真实 Redis 和 Channels

运行方式:
    cd backend/Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.common.tests.test_publish_event --verbosity=2
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.common import realtime_events
from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError
from Django_xm.common.realtime_events import _group_name


def _valid_tool_call_payload(**overrides):
    """构造合法的工具调用 payload（含 4 个必填字段 + parameters）。"""
    defaults = {
        'tool_call_id': 'tc-1',
        'tool_name': 'shell_exec',
        'source': EventSource.CHAT,
        'source_id': 'session-1',
        'parameters': {'command': 'ls'},
    }
    defaults.update(overrides)
    return defaults


def _valid_approval_payload(**overrides):
    """构造合法的审批 payload（含 6 个必填字段）。"""
    defaults = {
        'interrupt_id': 'intr-1',
        'tool_call_id': 'tc-1',
        'source': EventSource.DEEP_RESEARCH,
        'source_id': 'task-1',
        'state': 'pending',
        'parameters': {'command': 'test'},
    }
    defaults.update(overrides)
    return defaults


def _valid_stream_payload(**overrides):
    """构造合法的流式 payload。"""
    defaults = {
        'source': EventSource.CHAT,
        'source_id': 'session-1',
        'data': {'content': 'hello'},
    }
    defaults.update(overrides)
    return defaults


class PublishEventChannelRoutingTests(unittest.IsolatedAsyncioTestCase):
    """publish_event 频道路由测试。"""

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_only_session_id_publishes_to_session_channel(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """仅 session_id 参数 → 仅发布到 session 频道。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.TOOL_CALL_RUNNING,
            _valid_tool_call_payload(),
            session_id='session-1',
        )

        # _publish_to_session_async 被调用一次（session 频道）
        # 验证 group_send 调用的 group name 符合 _group_name("session", "session-1")
        mock_layer.group_send.assert_called_once()
        args, kwargs = mock_layer.group_send.call_args
        group_name = args[0] if args else kwargs.get('group')
        self.assertEqual(group_name, _group_name("session", "session-1"))

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_only_task_id_publishes_to_task_channel(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """仅 task_id 参数 → 仅发布到 task 频道。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.TOOL_CALL_RUNNING,
            _valid_tool_call_payload(source=EventSource.DEEP_RESEARCH, source_id='task-1'),
            task_id='task-1',
        )

        mock_layer.group_send.assert_called_once()
        args, _ = mock_layer.group_send.call_args
        group_name = args[0]
        self.assertEqual(group_name, _group_name("task", "task-1"))

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_only_user_id_publishes_to_user_channel(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """仅 user_id 参数 → 仅发布到 user 频道。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.SESSION_CREATED,
            {},
            user_id='user-1',
        )

        mock_layer.group_send.assert_called_once()
        args, _ = mock_layer.group_send.call_args
        group_name = args[0]
        self.assertEqual(group_name, _group_name("user", "user-1"))

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_session_and_task_dual_channel(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """session_id + task_id → 双频道发布。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.TOOL_CALL_RUNNING,
            _valid_tool_call_payload(),
            session_id='session-1',
            task_id='task-1',
        )

        # 应该有 2 次 group_send（session + task）
        self.assertEqual(mock_layer.group_send.call_count, 2)
        called_groups = [call.args[0] for call in mock_layer.group_send.call_args_list]
        self.assertIn(_group_name("session", "session-1"), called_groups)
        self.assertIn(_group_name("task", "task-1"), called_groups)

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_all_none_no_publish(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """三者都为 None → 不发布（不调用 _atomic_publish_event）。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.SESSION_CREATED,
            {},
        )

        mock_atomic.assert_not_called()
        mock_layer.group_send.assert_not_called()

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_three_channels_broadcast(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """session + task + user 三频道同时广播。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.TOOL_CALL_RUNNING,
            _valid_tool_call_payload(),
            session_id='session-1',
            task_id='task-1',
            user_id='user-1',
        )

        self.assertEqual(mock_layer.group_send.call_count, 3)


class PublishEventPayloadValidationTests(unittest.IsolatedAsyncioTestCase):
    """publish_event payload 校验测试。

    Task 1 修复后：校验失败时记录 error 并抛出 PayloadValidationError，
    不再静默 return（避免事件被丢弃且调用方无感知）。
    """

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_valid_payload_publishes_normally(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """合法 payload → 正常发布。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.TOOL_CALL_INPUT_READY,
            _valid_tool_call_payload(),
            session_id='session-1',
        )

        mock_atomic.assert_called_once()
        mock_layer.group_send.assert_called_once()

    @patch('Django_xm.common.realtime_events._atomic_publish_event')
    async def test_missing_required_field_no_publish(self, mock_atomic):
        """缺少必填字段 → 记录 error 并抛出 PayloadValidationError（不发布）。

        Task 1 修复：publish_event 校验失败从 warning + return 改为
        error + raise PayloadValidationError，避免事件被静默丢弃。
        """
        with patch('Django_xm.common.realtime_events.logger') as mock_logger:
            payload = _valid_tool_call_payload()
            payload.pop('parameters')  # 移除必填字段

            with self.assertRaises(PayloadValidationError):
                await realtime_events.publish_event(
                    EventType.TOOL_CALL_INPUT_READY,
                    payload,
                    session_id='session-1',
                )

            mock_atomic.assert_not_called()
            mock_logger.error.assert_called_once()

    @patch('Django_xm.common.realtime_events._atomic_publish_event')
    async def test_payload_not_dict_no_publish(self, mock_atomic):
        """payload 非 dict → 记录 error 并抛出 PayloadValidationError。

        Task 1 修复：校验失败改为 error + raise，不再 warning + return。
        """
        with patch('Django_xm.common.realtime_events.logger') as mock_logger:
            with self.assertRaises(PayloadValidationError):
                await realtime_events.publish_event(
                    EventType.TOOL_CALL_RUNNING,
                    'not-a-dict',  # 非 dict
                    session_id='session-1',
                )

            mock_atomic.assert_not_called()
            mock_logger.error.assert_called_once()

    @patch('Django_xm.common.realtime_events._atomic_publish_event')
    async def test_invalid_source_no_publish(self, mock_atomic):
        """source 字段非法 → 记录 error 并抛出 PayloadValidationError。

        Task 1 修复：校验失败改为 error + raise，不再 warning + return。
        """
        with patch('Django_xm.common.realtime_events.logger') as mock_logger:
            payload = _valid_tool_call_payload(source='invalid_source')

            with self.assertRaises(PayloadValidationError):
                await realtime_events.publish_event(
                    EventType.TOOL_CALL_RUNNING,
                    payload,
                    session_id='session-1',
                )

            mock_atomic.assert_not_called()
            mock_logger.error.assert_called_once()

    @patch('Django_xm.common.realtime_events._atomic_publish_event')
    async def test_invalid_event_type_no_publish(self, mock_atomic):
        """event_type 非 EventType 枚举 → 记录 error 并抛出 PayloadValidationError。

        Task 1 修复：校验失败改为 error + raise，不再 warning + return。
        """
        with patch('Django_xm.common.realtime_events.logger') as mock_logger:
            with self.assertRaises(PayloadValidationError):
                await realtime_events.publish_event(
                    'not_an_event_type',  # 非 EventType 枚举
                    _valid_tool_call_payload(),
                    session_id='session-1',
                )

            mock_atomic.assert_not_called()
            mock_logger.error.assert_called_once()

    @patch('Django_xm.common.realtime_events._atomic_publish_event')
    async def test_missing_required_field_raises_payload_validation_error(self, mock_atomic):
        """必填字段缺失时 publish_event 抛出 PayloadValidationError（Task 1 修复后行为）。

        Task 1 修复：publish_event 不再捕获并吞掉 PayloadValidationError，
        而是记录 error 后重新抛出，由调用方决定降级策略。
        本测试显式断言调用 publish_event 抛出 PayloadValidationError。
        """
        with patch('Django_xm.common.realtime_events.logger'):
            payload = _valid_tool_call_payload()
            payload.pop('tool_call_id')  # 移除核心必填字段

            with self.assertRaises(PayloadValidationError):
                await realtime_events.publish_event(
                    EventType.TOOL_CALL_PENDING,
                    payload,
                    session_id='session-1',
                )

            mock_atomic.assert_not_called()

    async def test_validate_payload_with_correct_field_types_passes(self):
        """字段类型正确时 validate_payload 直接通过校验（SubTask 8.2）。

        不通过 publish_event，直接调用 validate_payload 验证各事件类型的合法 payload。
        """
        from Django_xm.common.event_schema import validate_payload

        # TOOL_CALL_INPUT_READY：4 个核心字段 + parameters
        validate_payload(EventType.TOOL_CALL_INPUT_READY, _valid_tool_call_payload())

        # TOOL_CALL_FAILED：4 个核心字段 + parameters + error
        failed_payload = _valid_tool_call_payload()
        failed_payload['error'] = 'boom'
        validate_payload(EventType.TOOL_CALL_FAILED, failed_payload)

        # APPROVAL_PENDING：6 个核心字段（含 parameters）
        validate_payload(EventType.APPROVAL_PENDING, _valid_approval_payload())

        # STREAM_REASONING：source + source_id + data
        validate_payload(EventType.STREAM_REASONING, _valid_stream_payload())

    @patch('Django_xm.common.realtime_events._atomic_publish_event')
    async def test_validation_failure_raises_payload_validation_error(self, mock_atomic):
        """payload 校验失败抛出 PayloadValidationError（Task 1 修复后行为）。

        Task 1 修复：payload 完全非法（None）时 publish_event 抛出
        PayloadValidationError，不再静默返回。
        """
        with patch('Django_xm.common.realtime_events.logger'):
            with self.assertRaises(PayloadValidationError):
                await realtime_events.publish_event(
                    EventType.TOOL_CALL_RUNNING,
                    None,
                    session_id='session-1',
                )

            mock_atomic.assert_not_called()


class PublishEventErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    """publish_event 错误处理测试。"""

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_redis_connection_failure_no_raise(
        self, mock_atomic, mock_get_layer
    ):
        """Redis 连接失败（_get_redis_client 抛异常）→ 记录 error 并返回，不抛异常。

        注：_publish_to_session_async 内部 try/except 捕获 _get_redis_client 异常，
        仅记录日志不抛出。
        """
        # _publish_to_session_async 内部调用 _get_redis_client()
        with patch(
            'Django_xm.common.realtime_events._get_redis_client',
            side_effect=ConnectionError('redis down'),
        ), patch('Django_xm.common.realtime_events.logger') as mock_logger:
            # 不应抛出异常
            await realtime_events.publish_event(
                EventType.TOOL_CALL_RUNNING,
                _valid_tool_call_payload(),
                session_id='session-1',
            )

            # _atomic_publish_event 不应被调用（_get_redis_client 先失败）
            mock_atomic.assert_not_called()
            # 应记录 error 日志
            mock_logger.error.assert_called()

    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_group_send_failure_does_not_rollback_seq(
        self, mock_atomic, mock_get_redis
    ):
        """group_send 失败 → 记录 error 但 seq 已持久化（不回滚）。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock(side_effect=RuntimeError('channel layer down'))

        with patch(
            'Django_xm.common.realtime_events.get_channel_layer', return_value=mock_layer
        ), patch('Django_xm.common.realtime_events.logger') as mock_logger:
            # 不应抛出异常
            await realtime_events.publish_event(
                EventType.TOOL_CALL_RUNNING,
                _valid_tool_call_payload(),
                session_id='session-1',
            )

            # seq 已经通过 _atomic_publish_event 持久化（调用一次）
            mock_atomic.assert_called_once()
            # group_send 失败应记录 error 日志
            mock_logger.error.assert_called()
            # 验证 error 日志包含 group_send 失败信息
            error_calls = [str(c) for c in mock_logger.error.call_args_list]
            self.assertTrue(any('group_send' in c for c in error_calls))

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    async def test_atomic_publish_failure_no_raise(
        self, mock_get_redis, mock_get_layer
    ):
        """_atomic_publish_event 抛异常 → _publish_to_session_async 捕获并记录 error。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        with patch(
            'Django_xm.common.realtime_events._atomic_publish_event',
            side_effect=RuntimeError('lua script failed'),
        ), patch('Django_xm.common.realtime_events.logger') as mock_logger:
            # 不应抛出异常
            await realtime_events.publish_event(
                EventType.TOOL_CALL_RUNNING,
                _valid_tool_call_payload(),
                session_id='session-1',
            )

            # group_send 不应被调用（_atomic_publish_event 先失败）
            mock_layer.group_send.assert_not_called()
            mock_logger.error.assert_called()


class PublishEventSeqTests(unittest.IsolatedAsyncioTestCase):
    """publish_event seq 注入测试。"""

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=99)
    async def test_seq_injected_into_event(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """seq 由 _atomic_publish_event 返回并注入到事件的顶层。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.TOOL_CALL_RUNNING,
            _valid_tool_call_payload(),
            session_id='session-1',
        )

        # 验证 group_send 的事件包含 seq=99
        args, _ = mock_layer.group_send.call_args
        event_message = args[1]
        self.assertEqual(event_message['event']['seq'], 99)
        # 验证事件结构包含 type/timestamp/payload/seq
        event = event_message['event']
        self.assertIn('type', event)
        self.assertIn('timestamp', event)
        self.assertIn('payload', event)
        self.assertIn('seq', event)

    @patch('Django_xm.common.realtime_events.get_channel_layer')
    @patch('Django_xm.common.realtime_events._get_redis_client')
    @patch('Django_xm.common.realtime_events._atomic_publish_event', return_value=42)
    async def test_ws_event_name_used_in_event_type(
        self, mock_atomic, mock_get_redis, mock_get_layer
    ):
        """事件 type 字段使用 ws_event_name（如 STREAM_REASONING → 'stream_event'）。"""
        mock_layer = MagicMock()
        mock_layer.group_send = AsyncMock()
        mock_get_layer.return_value = mock_layer

        await realtime_events.publish_event(
            EventType.STREAM_REASONING,
            _valid_stream_payload(),
            session_id='session-1',
        )

        args, _ = mock_layer.group_send.call_args
        event = args[1]['event']
        self.assertEqual(event['type'], 'stream_event')


class PublishEventSyncTests(SimpleTestCase):
    """publish_event_sync 同步版本测试。

    Task 1 修复后：publish_event_sync 不再吞 PayloadValidationError（重新抛出），
    但仍吞其他异常（如 RuntimeError）以保证业务流程不中断。
    """

    @patch('Django_xm.common.realtime_events.publish_event', new_callable=AsyncMock)
    def test_sync_calls_async_publish(self, mock_async):
        """publish_event_sync 通过 async_to_sync 调用 publish_event。"""
        realtime_events.publish_event_sync(
            EventType.SESSION_CREATED,
            {},
            session_id='session-1',
        )
        mock_async.assert_called_once()
        args, kwargs = mock_async.call_args
        self.assertEqual(args[0], EventType.SESSION_CREATED)
        self.assertEqual(kwargs['session_id'], 'session-1')

    @patch('Django_xm.common.realtime_events.publish_event', new_callable=AsyncMock)
    def test_sync_reraises_payload_validation_error(self, mock_async):
        """publish_event_sync 不吞 PayloadValidationError，重新抛出（Task 1 修复后行为）。

        Task 1 修复：publish_event_sync 在 except PayloadValidationError 分支中
        记录 error 并 raise，确保调用方感知校验失败。
        PayloadValidationError 必须在 except Exception 之前捕获，避免被吞。
        """
        mock_async.side_effect = PayloadValidationError('validation failed')

        with patch('Django_xm.common.realtime_events.logger'), self.assertRaises(PayloadValidationError):
            realtime_events.publish_event_sync(
                EventType.SESSION_CREATED,
                {},
                session_id='session-1',
            )

    @patch('Django_xm.common.realtime_events.publish_event', new_callable=AsyncMock)
    def test_sync_swallows_other_exceptions(self, mock_async):
        """publish_event_sync 仍吞 RuntimeError 等非 PayloadValidationError 异常。

        Task 1 修复：仅 PayloadValidationError 被重新抛出，其他异常仍被
        except Exception 分支捕获并记录 error 日志，避免阻塞调用方业务流程。
        """
        mock_async.side_effect = RuntimeError('async error')
        # 不应抛出
        with patch('Django_xm.common.realtime_events.logger') as mock_logger:
            realtime_events.publish_event_sync(
                EventType.SESSION_CREATED,
                {},
                session_id='session-1',
            )
            # RuntimeError 被吞后应记录 error 日志
            mock_logger.error.assert_called()


class GetEventHistoryTests(SimpleTestCase):
    """get_event_history / get_channel_seq 测试。"""

    @patch('Django_xm.common.realtime_events._get_redis_client')
    def test_get_event_history_returns_list(self, mock_get_redis):
        """get_event_history 返回事件列表。"""
        mock_redis = MagicMock()
        mock_redis.lrange.return_value = [
            b'{"seq": 1, "type": "session_created", "payload": {}}',
            b'{"seq": 2, "type": "message_added", "payload": {}}',
        ]
        mock_get_redis.return_value = mock_redis

        result = realtime_events.get_event_history('session', 'session-1')
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['seq'], 1)
        self.assertEqual(result[1]['seq'], 2)

    @patch('Django_xm.common.realtime_events._get_redis_client')
    def test_get_event_history_filters_by_last_seq(self, mock_get_redis):
        """last_seq 过滤 seq <= last_seq 的事件。"""
        mock_redis = MagicMock()
        mock_redis.lrange.return_value = [
            b'{"seq": 1, "type": "a"}',
            b'{"seq": 2, "type": "b"}',
            b'{"seq": 3, "type": "c"}',
        ]
        mock_get_redis.return_value = mock_redis

        result = realtime_events.get_event_history('session', 'session-1', last_seq=1)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['seq'], 2)
        self.assertEqual(result[1]['seq'], 3)

    @patch('Django_xm.common.realtime_events._get_redis_client')
    def test_get_event_history_handles_invalid_json(self, mock_get_redis):
        """无效 JSON 行被跳过，不抛异常。"""
        mock_redis = MagicMock()
        mock_redis.lrange.return_value = [
            b'{"seq": 1, "type": "a"}',
            b'invalid json',
            b'{"seq": 2, "type": "b"}',
        ]
        mock_get_redis.return_value = mock_redis

        result = realtime_events.get_event_history('session', 'session-1')
        self.assertEqual(len(result), 2)

    @patch('Django_xm.common.realtime_events._get_redis_client')
    def test_get_event_history_redis_failure_returns_empty(self, mock_get_redis):
        """Redis 异常返回空列表，不抛异常。"""
        mock_get_redis.side_effect = ConnectionError('redis down')
        result = realtime_events.get_event_history('session', 'session-1')
        self.assertEqual(result, [])

    @patch('Django_xm.common.realtime_events._get_redis_client')
    def test_get_channel_seq_returns_int(self, mock_get_redis):
        """get_channel_seq 返回 int。"""
        mock_redis = MagicMock()
        mock_redis.get.return_value = b'42'
        mock_get_redis.return_value = mock_redis

        self.assertEqual(realtime_events.get_channel_seq('session', 'session-1'), 42)

    @patch('Django_xm.common.realtime_events._get_redis_client')
    def test_get_channel_seq_returns_zero_when_no_key(self, mock_get_redis):
        """Redis 无 key 返回 0。"""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        self.assertEqual(realtime_events.get_channel_seq('session', 'session-1'), 0)

    @patch('Django_xm.common.realtime_events._get_redis_client')
    def test_get_channel_seq_redis_failure_returns_zero(self, mock_get_redis):
        """Redis 异常返回 0。"""
        mock_get_redis.side_effect = ConnectionError('redis down')
        self.assertEqual(realtime_events.get_channel_seq('session', 'session-1'), 0)


if __name__ == '__main__':
    unittest.main()
