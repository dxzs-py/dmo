"""event_schema payload 校验函数单元测试。

覆盖:
- validate_payload（合法/非法 payload、必填字段、source 字段、event_type 校验）
- get_ws_event_name（事件类型到 ws_event_name 映射）
- is_tool_lifecycle_event / is_approval_event / is_stream_event（事件分类）
- EventType.from_value / EventSource.from_value（枚举反向构造）

运行方式:
    cd backend/Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.common.tests.test_event_schema --verbosity=2
"""

from __future__ import annotations

import unittest

from Django_xm.common.event_schema import (
    EventSource,
    EventType,
    PayloadValidationError,
    get_ws_event_name,
    is_approval_event,
    is_stream_event,
    is_tool_lifecycle_event,
    validate_payload,
)


def _tool_call_payload(**overrides):
    """构造工具调用 payload，默认包含 4 个必填字段 + parameters。"""
    defaults = {
        'tool_call_id': 'tc-1',
        'tool_name': 'shell_exec',
        'source': EventSource.CHAT,
        'source_id': 'session-1',
        'parameters': {'command': 'ls'},
    }
    defaults.update(overrides)
    return defaults


def _approval_payload(**overrides):
    """构造审批 payload，默认包含 6 个必填字段（含 parameters）。"""
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


def _stream_payload(**overrides):
    """构造流式 payload，默认包含 source/source_id/data。"""
    defaults = {
        'source': EventSource.CHAT,
        'source_id': 'session-1',
        'data': {'content': 'hello'},
    }
    defaults.update(overrides)
    return defaults


class ValidatePayloadTests(unittest.TestCase):
    """validate_payload 校验测试。"""

    # === 合法 payload ===

    def test_tool_call_input_ready_valid(self):
        """TOOL_CALL_INPUT_READY 合法 payload 通过校验。"""
        validate_payload(EventType.TOOL_CALL_INPUT_READY, _tool_call_payload())

    def test_tool_call_completed_valid(self):
        """TOOL_CALL_COMPLETED 合法 payload 通过校验。"""
        payload = _tool_call_payload()
        payload['result'] = {'output': 'ok'}
        validate_payload(EventType.TOOL_CALL_COMPLETED, payload)

    def test_approval_pending_valid(self):
        """APPROVAL_PENDING 合法 payload 通过校验。"""
        validate_payload(EventType.APPROVAL_PENDING, _approval_payload())

    def test_stream_reasoning_valid(self):
        """STREAM_REASONING 合法 payload 通过校验。"""
        validate_payload(EventType.STREAM_REASONING, _stream_payload())

    def test_session_created_no_required_fields(self):
        """SESSION_CREATED 无必填字段，空 payload 也能通过。"""
        validate_payload(EventType.SESSION_CREATED, {})

    def test_message_added_no_required_fields(self):
        """MESSAGE_ADDED 无必填字段，空 payload 也能通过。"""
        validate_payload(EventType.MESSAGE_ADDED, {})

    def test_source_as_enum_string(self):
        """source 字段为 EventSource 枚举值（str 子类）通过校验。"""
        payload = _tool_call_payload(source=EventSource.LEARNING)
        validate_payload(EventType.TOOL_CALL_RUNNING, payload)

    def test_source_as_plain_string(self):
        """source 字段为合法字符串（如 'chat'）通过校验。"""
        payload = _tool_call_payload(source='chat')
        validate_payload(EventType.TOOL_CALL_RUNNING, payload)

    def test_source_event_source_chat_passes(self):
        """source 字段为 EventSource.CHAT 时校验通过（SubTask 8.5）。

        显式断言 CHAT 来源 payload 通过校验，覆盖聊天场景的最常用路径。
        """
        payload = _tool_call_payload(source=EventSource.CHAT)
        # 不抛异常即通过
        validate_payload(EventType.TOOL_CALL_INPUT_READY, payload)
        validate_payload(EventType.TOOL_CALL_RUNNING, payload)

    def test_source_event_source_deep_research_passes(self):
        """source 字段为 EventSource.DEEP_RESEARCH 时校验通过（SubTask 8.5 补充）。"""
        payload = _tool_call_payload(
            source=EventSource.DEEP_RESEARCH, source_id='task-1'
        )
        validate_payload(EventType.TOOL_CALL_RUNNING, payload)

    def test_source_invalid_string_raises_payload_validation_error(self):
        """source 字段为非法字符串抛 PayloadValidationError（SubTask 8.5 反向用例）。

        注：PayloadValidationError 继承自 Exception（非 ValueError）。
        本测试显式断言抛出异常类型为 PayloadValidationError。
        """
        payload = _tool_call_payload(source='invalid_source')
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.TOOL_CALL_RUNNING, payload)
        self.assertIn('source', str(ctx.exception))

    # === 缺少必填字段 ===

    def test_tool_call_input_ready_missing_parameters(self):
        """TOOL_CALL_INPUT_READY 缺少 parameters 抛 PayloadValidationError。"""
        payload = _tool_call_payload()
        payload.pop('parameters')
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.TOOL_CALL_INPUT_READY, payload)
        self.assertIn('parameters', str(ctx.exception))

    def test_tool_call_failed_missing_error(self):
        """TOOL_CALL_FAILED 缺少 error 抛 PayloadValidationError。"""
        payload = _tool_call_payload()
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.TOOL_CALL_FAILED, payload)
        self.assertIn('error', str(ctx.exception))

    def test_approval_pending_missing_state(self):
        """APPROVAL_PENDING 缺少 state 抛 PayloadValidationError。"""
        payload = _approval_payload()
        payload.pop('state')
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.APPROVAL_PENDING, payload)
        self.assertIn('state', str(ctx.exception))

    def test_approval_pending_missing_interrupt_id(self):
        """APPROVAL_PENDING 缺少 interrupt_id 抛 PayloadValidationError（SubTask 8.4 补充）。"""
        payload = _approval_payload()
        payload.pop('interrupt_id')
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.APPROVAL_PENDING, payload)
        self.assertIn('interrupt_id', str(ctx.exception))

    def test_tool_call_failed_missing_error_message(self):
        """TOOL_CALL_FAILED 缺少 error 字段抛 PayloadValidationError（SubTask 8.4 显式断言）。

        与 test_tool_call_failed_missing_error 互补：显式构造失败 payload
        （保留 parameters 也不影响 error 必填校验）。
        """
        payload = _tool_call_payload()
        # TOOL_CALL_FAILED 必填字段包含 error，但默认 payload 不含 error
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.TOOL_CALL_FAILED, payload)
        self.assertIn('error', str(ctx.exception))

    def test_stream_reasoning_missing_data(self):
        """STREAM_REASONING 缺少 data 抛 PayloadValidationError。"""
        payload = _stream_payload()
        payload.pop('data')
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.STREAM_REASONING, payload)
        self.assertIn('data', str(ctx.exception))

    def test_required_field_none_treated_as_missing(self):
        """必填字段值为 None 视为缺失。"""
        payload = _tool_call_payload(tool_name=None)
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.TOOL_CALL_PENDING, payload)
        self.assertIn('tool_name', str(ctx.exception))

    # === event_type / payload 类型校验 ===

    def test_event_type_not_enum_raises(self):
        """event_type 非 EventType 枚举抛 PayloadValidationError。"""
        with self.assertRaises(PayloadValidationError):
            validate_payload('tool_call_running', _tool_call_payload())

    def test_payload_not_dict_raises(self):
        """payload 非 dict 抛 PayloadValidationError。"""
        with self.assertRaises(PayloadValidationError):
            validate_payload(EventType.TOOL_CALL_RUNNING, 'not-a-dict')

    def test_payload_none_raises(self):
        """payload 为 None 抛 PayloadValidationError。"""
        with self.assertRaises(PayloadValidationError):
            validate_payload(EventType.TOOL_CALL_RUNNING, None)

    # === source 字段校验 ===

    def test_source_invalid_string_raises(self):
        """source 字段为非法字符串抛 PayloadValidationError。"""
        payload = _tool_call_payload(source='invalid_source')
        with self.assertRaises(PayloadValidationError) as ctx:
            validate_payload(EventType.TOOL_CALL_RUNNING, payload)
        self.assertIn('source', str(ctx.exception))

    def test_source_int_raises(self):
        """source 字段为非字符串类型（int）抛 PayloadValidationError。"""
        payload = _tool_call_payload(source=123)
        with self.assertRaises(PayloadValidationError):
            validate_payload(EventType.TOOL_CALL_RUNNING, payload)

    def test_source_none_skipped(self):
        """source 字段为 None 时跳过校验（由必填字段检查负责）。"""
        payload = _tool_call_payload()
        # tool_call 事件必填字段包含 source，所以 source=None 会先在必填字段检查中抛出
        with self.assertRaises(PayloadValidationError):
            validate_payload(EventType.TOOL_CALL_RUNNING, payload | {'source': None})

    def test_session_created_skips_source_check(self):
        """SESSION_CREATED 无必填字段，source 校验也跳过。"""
        validate_payload(EventType.SESSION_CREATED, {'source': 'invalid_source'})

    def test_session_updated_no_required_fields_skips_check(self):
        """SESSION_UPDATED 无必填字段跳过校验（SubTask 8.6）。"""
        validate_payload(EventType.SESSION_UPDATED, {})
        validate_payload(EventType.SESSION_UPDATED, {'random_field': 'ok'})

    def test_session_deleted_no_required_fields_skips_check(self):
        """SESSION_DELETED 无必填字段跳过校验（SubTask 8.6）。"""
        validate_payload(EventType.SESSION_DELETED, {})

    def test_message_updated_no_required_fields_skips_check(self):
        """MESSAGE_UPDATED 无必填字段跳过校验（SubTask 8.6）。"""
        validate_payload(EventType.MESSAGE_UPDATED, {})

    def test_message_deleted_no_required_fields_skips_check(self):
        """MESSAGE_DELETED 无必填字段跳过校验（SubTask 8.6）。"""
        validate_payload(EventType.MESSAGE_DELETED, {})

    def test_messages_deleted_no_required_fields_skips_check(self):
        """MESSAGES_DELETED 无必填字段跳过校验（SubTask 8.6）。"""
        validate_payload(EventType.MESSAGES_DELETED, {})

    def test_stream_started_still_requires_source_and_source_id(self):
        """STREAM_STARTED 实际有 source/source_id 必填字段（SubTask 8.6 行为校验）。

        任务描述将 STREAM_STARTED 归为"无必填字段跳过校验"，但实际
        ``_REQUIRED_FIELDS`` 中 STREAM_STARTED: ('source', 'source_id')，
        ``validate_payload`` 不会跳过此事件类型的必填字段检查。
        本测试覆盖其实际行为，确保缺失 source/source_id 时抛出异常。
        """
        # 缺失 source/source_id 抛 PayloadValidationError
        with self.assertRaises(PayloadValidationError):
            validate_payload(EventType.STREAM_STARTED, {})

        # 提供 source/source_id 通过校验
        validate_payload(EventType.STREAM_STARTED, {
            'source': EventSource.CHAT,
            'source_id': 'session-1',
        })


class GetWsEventNameTests(unittest.TestCase):
    """get_ws_event_name 映射测试。"""

    def test_tool_call_pending(self):
        self.assertEqual(
            get_ws_event_name(EventType.TOOL_CALL_PENDING), 'tool_call_pending'
        )

    def test_approval_pending(self):
        self.assertEqual(
            get_ws_event_name(EventType.APPROVAL_PENDING), 'approval_pending'
        )

    def test_stream_reasoning_mapped_to_stream_event(self):
        """STREAM_REASONING 等 5 个流式子事件统一映射为 'stream_event'。"""
        self.assertEqual(get_ws_event_name(EventType.STREAM_REASONING), 'stream_event')
        self.assertEqual(get_ws_event_name(EventType.STREAM_SOURCES), 'stream_event')
        self.assertEqual(get_ws_event_name(EventType.STREAM_SUGGESTIONS), 'stream_event')
        self.assertEqual(get_ws_event_name(EventType.STREAM_CONTEXT), 'stream_event')
        self.assertEqual(
            get_ws_event_name(EventType.STREAM_CONTENT_UPDATE), 'stream_event'
        )

    def test_stream_started(self):
        self.assertEqual(get_ws_event_name(EventType.STREAM_STARTED), 'stream_started')

    def test_stream_completed(self):
        self.assertEqual(get_ws_event_name(EventType.STREAM_COMPLETED), 'stream_completed')

    def test_session_created(self):
        self.assertEqual(get_ws_event_name(EventType.SESSION_CREATED), 'session_created')

    def test_message_updated(self):
        self.assertEqual(get_ws_event_name(EventType.MESSAGE_UPDATED), 'message_updated')

    def test_message_regenerated(self):
        self.assertEqual(
            get_ws_event_name(EventType.MESSAGE_REGENERATED), 'message_regenerated'
        )


class EventCategoryTests(unittest.TestCase):
    """is_tool_lifecycle_event / is_approval_event / is_stream_event 测试。"""

    def test_is_tool_lifecycle_event_true(self):
        for et in (
            EventType.TOOL_CALL_PENDING,
            EventType.TOOL_CALL_INPUT_READY,
            EventType.TOOL_CALL_WAITING,
            EventType.TOOL_CALL_RUNNING,
            EventType.TOOL_CALL_COMPLETED,
            EventType.TOOL_CALL_FAILED,
            EventType.TOOL_CALL_TIMEOUT,
        ):
            with self.subTest(event_type=et):
                self.assertTrue(is_tool_lifecycle_event(et))

    def test_is_tool_lifecycle_event_false(self):
        self.assertFalse(is_tool_lifecycle_event(EventType.APPROVAL_PENDING))
        self.assertFalse(is_tool_lifecycle_event(EventType.STREAM_REASONING))
        self.assertFalse(is_tool_lifecycle_event(EventType.SESSION_CREATED))

    def test_is_approval_event_true(self):
        for et in (
            EventType.APPROVAL_PENDING,
            EventType.APPROVAL_PROCESSING,
            EventType.APPROVAL_APPROVED,
            EventType.APPROVAL_REJECTED,
            EventType.APPROVAL_TIMEOUT,
        ):
            with self.subTest(event_type=et):
                self.assertTrue(is_approval_event(et))

    def test_is_approval_event_false(self):
        self.assertFalse(is_approval_event(EventType.TOOL_CALL_PENDING))
        self.assertFalse(is_approval_event(EventType.STREAM_REASONING))

    def test_is_stream_event_true(self):
        for et in (
            EventType.STREAM_REASONING,
            EventType.STREAM_SOURCES,
            EventType.STREAM_SUGGESTIONS,
            EventType.STREAM_CONTEXT,
            EventType.STREAM_CONTENT_UPDATE,
        ):
            with self.subTest(event_type=et):
                self.assertTrue(is_stream_event(et))

    def test_is_stream_event_false_for_started_completed(self):
        """STREAM_STARTED / STREAM_COMPLETED / STREAM_FINALIZED 不属于 StreamPayload。"""
        self.assertFalse(is_stream_event(EventType.STREAM_STARTED))
        self.assertFalse(is_stream_event(EventType.STREAM_COMPLETED))
        self.assertFalse(is_stream_event(EventType.STREAM_FINALIZED))


class FromValueTests(unittest.TestCase):
    """EventType.from_value / EventSource.from_value 反向构造测试。"""

    def test_event_type_from_value_valid(self):
        self.assertEqual(
            EventType.from_value('tool_call_running'), EventType.TOOL_CALL_RUNNING
        )
        self.assertEqual(
            EventType.from_value('approval_pending'), EventType.APPROVAL_PENDING
        )
        self.assertEqual(
            EventType.from_value('session_created'), EventType.SESSION_CREATED
        )

    def test_event_type_from_value_invalid_returns_none(self):
        """无效字符串返回 None，不抛异常。"""
        self.assertIsNone(EventType.from_value('not_an_event'))
        self.assertIsNone(EventType.from_value(''))

    def test_event_source_from_value_valid(self):
        self.assertEqual(EventSource.from_value('chat'), EventSource.CHAT)
        self.assertEqual(
            EventSource.from_value('deep_research'), EventSource.DEEP_RESEARCH
        )
        self.assertEqual(EventSource.from_value('learning'), EventSource.LEARNING)

    def test_event_source_from_value_invalid_returns_none(self):
        self.assertIsNone(EventSource.from_value('invalid'))
        self.assertIsNone(EventSource.from_value(''))


class EventTypeStrEnumTests(unittest.TestCase):
    """EventType 作为 str + Enum 的行为测试（保证 JSON 序列化稳定）。"""

    def test_event_type_equals_string(self):
        """EventType 枚举值可直接与字符串比较。"""
        self.assertEqual(EventType.TOOL_CALL_RUNNING, 'tool_call_running')

    def test_event_type_in_set_lookup(self):
        """EventType 可作为 set 成员，字符串也能匹配。"""
        s = {EventType.TOOL_CALL_PENDING, EventType.TOOL_CALL_RUNNING}
        self.assertIn('tool_call_pending', s)
        self.assertIn(EventType.TOOL_CALL_RUNNING, s)

    def test_event_source_equals_string(self):
        self.assertEqual(EventSource.CHAT, 'chat')


class PayloadValidationErrorTypeTests(unittest.TestCase):
    """PayloadValidationError 异常类型测试（SubTask 8.2 类型契约校验）。

    任务描述中"validate_payload 抛出 ValueError"基于过时假设。
    实际被测代码中 PayloadValidationError 继承自 Exception（非 ValueError）。
    本测试类明确记录这一行为契约，便于后续维护时识别任务描述与实现的差异。
    """

    def test_payload_validation_error_is_exception_subclass(self):
        """PayloadValidationError 是 Exception 的子类。"""
        self.assertTrue(issubclass(PayloadValidationError, Exception))

    def test_payload_validation_error_not_value_error_subclass(self):
        """PayloadValidationError 不是 ValueError 的子类。

        若后续需要 ValueError 兼容（如通用异常处理），可在 event_schema.py
        修改 PayloadValidationError 继承 ValueError，本测试会失败提示更新。
        """
        self.assertFalse(issubclass(PayloadValidationError, ValueError))

    def test_payload_validation_error_caught_as_exception(self):
        """PayloadValidationError 可被 except Exception 捕获。"""
        try:
            raise PayloadValidationError("test message")
        except Exception as e:
            self.assertEqual(str(e), "test message")
        else:
            self.fail("PayloadValidationError 未被 except Exception 捕获")


if __name__ == '__main__':
    unittest.main()
