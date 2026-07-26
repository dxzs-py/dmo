"""stream_helpers 工具事件发布与 approval 合并单元测试。

覆盖:
- _publish_tool_lifecycle_event：parameters 透传给 service.transition（空 dict → None）
- _publish_tool_lifecycle_event：result / error 字段透传给 service.transition
- _publish_tool_lifecycle_event：register 的 ToolCallContext 核心字段
- _publish_tool_lifecycle_event：跳过逻辑（session_id / tool_call_id / tool_name 缺失）
- merge_existing_approval_fields：按 tool_call_id 索引合并

mock 策略:
- mock Django_xm.apps.chat.services.stream_helpers.service（ToolCallLifecycleService 单例）
- 通过 service.register / service.transition 的 call_args 验证字段透传
- 不依赖真实 Redis / Channels / Django ORM

运行方式:
    cd backend/Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.chat.tests.test_stream_helpers_tool_events --verbosity=2
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from Django_xm.apps.chat.services import stream_helpers
from Django_xm.apps.chat.services.stream_helpers import (
    _publish_tool_lifecycle_event,
    merge_existing_approval_fields,
)
from Django_xm.common.event_schema import EventType, EventSource


def _build_tool_info(**overrides):
    """构造 stream_helpers 内部使用的 tool_info dict。"""
    defaults = {
        'id': 'tc-1',
        'name': 'shell_exec',
        'parameters': {'command': 'ls'},
        'result': None,
        'error': None,
    }
    defaults.update(overrides)
    return defaults


class PublishToolLifecycleEventInputReadyTests(unittest.TestCase):
    """_publish_tool_lifecycle_event parameters 透传测试。

    _publish_tool_lifecycle_event 将 parameters 传给：
    - service.register：始终为 dict（空时为 {}），存入 ToolCallContext
    - service.transition：空 dict 时为 None（falsy 判断），非空时传 dict

    最终 payload 中 parameters 始终包含由 publish_tool_call_sync 保证（从 context 读取），
    不在 _publish_tool_lifecycle_event 层断言。
    """

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_input_ready_with_empty_parameters_passes_none_to_transition(self, mock_service):
        """INPUT_READY + parameters={} → transition parameters=None（空 dict 是 falsy）。"""
        tool_info = _build_tool_info(parameters={})

        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_INPUT_READY,
            tool_info,
            session_id='session-1',
            message_id='msg-1',
        )

        mock_service.transition.assert_called_once()
        _, kwargs = mock_service.transition.call_args
        self.assertIsNone(kwargs['parameters'])

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_input_ready_with_non_empty_parameters_passes_them(self, mock_service):
        """INPUT_READY + parameters={'command': 'ls'} → transition parameters={'command': 'ls'}。"""
        tool_info = _build_tool_info(parameters={'command': 'ls'})

        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_INPUT_READY,
            tool_info,
            session_id='session-1',
            message_id='msg-1',
        )

        mock_service.transition.assert_called_once()
        _, kwargs = mock_service.transition.call_args
        self.assertEqual(kwargs['parameters'], {'command': 'ls'})

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_input_ready_with_none_parameters_passes_none_to_transition(self, mock_service):
        """INPUT_READY + parameters=None → transition parameters=None。"""
        tool_info = _build_tool_info(parameters=None)

        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_INPUT_READY,
            tool_info,
            session_id='session-1',
            message_id='msg-1',
        )

        mock_service.transition.assert_called_once()
        _, kwargs = mock_service.transition.call_args
        self.assertIsNone(kwargs['parameters'])


class PublishToolLifecycleEventOtherEventTypesTests(unittest.TestCase):
    """其他事件类型（RUNNING/COMPLETED/FAILED）的 parameters / result / error 透传测试。"""

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_running_with_empty_parameters_passes_none_to_transition(self, mock_service):
        """RUNNING + parameters={} → transition parameters=None。"""
        tool_info = _build_tool_info(parameters={})

        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_RUNNING,
            tool_info,
            session_id='session-1',
            message_id='msg-1',
        )

        mock_service.transition.assert_called_once()
        _, kwargs = mock_service.transition.call_args
        self.assertIsNone(kwargs['parameters'])

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_running_with_non_empty_parameters_passes_them(self, mock_service):
        """RUNNING + parameters={'command': 'ls'} → transition parameters={'command': 'ls'}。"""
        tool_info = _build_tool_info(parameters={'command': 'ls'})

        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_RUNNING,
            tool_info,
            session_id='session-1',
            message_id='msg-1',
        )

        mock_service.transition.assert_called_once()
        _, kwargs = mock_service.transition.call_args
        self.assertEqual(kwargs['parameters'], {'command': 'ls'})

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_completed_with_result_passes_result_to_transition(self, mock_service):
        """COMPLETED + result={'output': 'ok'} → transition result={'output': 'ok'}。"""
        tool_info = _build_tool_info(
            parameters={'command': 'ls'},
            result={'output': 'ok'},
        )

        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_COMPLETED,
            tool_info,
            session_id='session-1',
            message_id='msg-1',
        )

        mock_service.transition.assert_called_once()
        _, kwargs = mock_service.transition.call_args
        self.assertEqual(kwargs['result'], {'output': 'ok'})

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_failed_with_error_passes_error_to_transition(self, mock_service):
        """FAILED + error='boom' → transition error='boom'。"""
        tool_info = _build_tool_info(
            parameters={'command': 'ls'},
            error='boom',
        )

        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_FAILED,
            tool_info,
            session_id='session-1',
            message_id='msg-1',
        )

        mock_service.transition.assert_called_once()
        _, kwargs = mock_service.transition.call_args
        self.assertEqual(kwargs['error'], 'boom')


class PublishToolLifecycleEventPayloadFieldsTests(unittest.TestCase):
    """_publish_tool_lifecycle_event register/transition 调用参数与跳过逻辑测试。"""

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_register_includes_core_required_fields(self, mock_service):
        """register 的 ToolCallContext 包含 tool_call_id / tool_name / module / module_id / message_id。"""
        tool_info = _build_tool_info()

        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_INPUT_READY,
            tool_info,
            session_id='session-1',
            message_id='msg-1',
        )

        mock_service.register.assert_called_once()
        ctx = mock_service.register.call_args.args[0]
        self.assertEqual(ctx.tool_call_id, 'tc-1')
        self.assertEqual(ctx.tool_name, 'shell_exec')
        self.assertEqual(ctx.module, EventSource.CHAT)
        self.assertEqual(ctx.module_id, 'session-1')
        self.assertEqual(ctx.message_id, 'msg-1')
        self.assertEqual(ctx.parameters, {'command': 'ls'})

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_no_session_id_skips_publish(self, mock_service):
        """session_id 为 None → 跳过发布（不调用 register/transition）。"""
        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_INPUT_READY,
            _build_tool_info(),
            session_id=None,
            message_id='msg-1',
        )
        mock_service.register.assert_not_called()
        mock_service.transition.assert_not_called()

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_missing_tool_call_id_skips_publish(self, mock_service):
        """tool_info 缺少 id → 跳过发布。"""
        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_INPUT_READY,
            _build_tool_info(id=''),  # 空 id
            session_id='session-1',
            message_id='msg-1',
        )
        mock_service.register.assert_not_called()
        mock_service.transition.assert_not_called()

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_missing_tool_name_skips_publish(self, mock_service):
        """tool_info 缺少 name → 跳过发布。"""
        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_INPUT_READY,
            _build_tool_info(name=''),  # 空 name
            session_id='session-1',
            message_id='msg-1',
        )
        mock_service.register.assert_not_called()
        mock_service.transition.assert_not_called()

    @patch('Django_xm.apps.chat.services.stream_helpers.service')
    def test_message_id_none_passes_empty_string_to_register(self, mock_service):
        """message_id 为 None → register 的 ToolCallContext.message_id=''，且不调用 bind_message_id。"""
        _publish_tool_lifecycle_event(
            EventType.TOOL_CALL_INPUT_READY,
            _build_tool_info(),
            session_id='session-1',
            message_id=None,
        )

        mock_service.register.assert_called_once()
        ctx = mock_service.register.call_args.args[0]
        self.assertEqual(ctx.message_id, '')
        # message_id 为 None 时 _publish_tool_lifecycle_event 不调用 bind_message_id
        mock_service.bind_message_id.assert_not_called()


class MergeExistingApprovalFieldsTests(unittest.TestCase):
    """merge_existing_approval_fields 测试。

    Task 1 修复：按 tool_call_id 索引合并（不再按位置 zip，不再长度检查跳过）。
    匹配策略：按 tool_call_id（优先）或 id（降级）构建索引，
    遍历 persisted_tool_calls 在 existing 索引中查找对应工具，
    仅当 persisted 条目缺少 approval 字段时从 existing 补充，已有 approval 不被覆盖。
    """

    def test_equal_length_matching_ids_merges_approval(self):
        """长度相等 + tool_call_id 匹配 → 旧 approval 状态合并到新 tool_calls。"""
        persisted = [
            {'id': 'tc-1', 'name': 'shell_exec', 'args': {}},
            {'id': 'tc-2', 'name': 'file_read', 'args': {}},
        ]
        existing = [
            {'id': 'tc-1', 'name': 'shell_exec', 'approval': {'state': 'approved'}},
            {'id': 'tc-2', 'name': 'file_read', 'approval': {'state': 'rejected'}},
        ]

        merge_existing_approval_fields(persisted, existing)

        self.assertEqual(persisted[0]['approval'], {'state': 'approved'})
        self.assertEqual(persisted[1]['approval'], {'state': 'rejected'})

    def test_persisted_longer_than_existing_merges_intersection_only(self):
        """长度不等（新增工具）：existing=2, persisted=3 → 前 2 个合并，第 3 个保留原值。

        Task 1 修复：不再因长度不等跳过合并，仅合并 existing_index 中存在的条目。
        """
        persisted = [
            {'id': 'tc-1', 'name': 'shell_exec', 'args': {}},
            {'id': 'tc-2', 'name': 'file_read', 'args': {}},
            {'id': 'tc-3', 'name': 'new_tool', 'args': {}},
        ]
        existing = [
            {'id': 'tc-1', 'name': 'shell_exec', 'approval': {'state': 'approved'}},
            {'id': 'tc-2', 'name': 'file_read', 'approval': {'state': 'rejected'}},
        ]

        merge_existing_approval_fields(persisted, existing)

        self.assertEqual(persisted[0]['approval'], {'state': 'approved'})
        self.assertEqual(persisted[1]['approval'], {'state': 'rejected'})
        # 新增工具保留 persisted 原值（无 approval 字段）
        self.assertNotIn('approval', persisted[2])

    def test_existing_longer_than_persisted_discards_extra(self):
        """长度不等（删除工具）：existing=3, persisted=2 → 前 2 个合并，第 3 个 existing 丢弃。"""
        persisted = [
            {'id': 'tc-1', 'name': 'shell_exec', 'args': {}},
            {'id': 'tc-2', 'name': 'file_read', 'args': {}},
        ]
        existing = [
            {'id': 'tc-1', 'name': 'shell_exec', 'approval': {'state': 'approved'}},
            {'id': 'tc-2', 'name': 'file_read', 'approval': {'state': 'rejected'}},
            {'id': 'tc-3', 'name': 'removed_tool', 'approval': {'state': 'approved'}},
        ]

        merge_existing_approval_fields(persisted, existing)

        self.assertEqual(persisted[0]['approval'], {'state': 'approved'})
        self.assertEqual(persisted[1]['approval'], {'state': 'rejected'})
        # 第 3 个 existing 工具被丢弃，不修改 persisted
        self.assertEqual(len(persisted), 2)

    def test_shuffled_order_merges_by_tool_call_id_index(self):
        """tool_call_id 顺序错乱 → 按 tool_call_id 索引正确合并（不按位置）。

        Task 1 修复：旧实现按位置 zip 导致顺序错乱时合并错误，
        新实现按 tool_call_id 索引，顺序无关。
        """
        persisted = [
            {'id': 'tc-1', 'name': 'shell_exec', 'args': {}},
            {'id': 'tc-2', 'name': 'file_read', 'args': {}},
            {'id': 'tc-3', 'name': 'third_tool', 'args': {}},
        ]
        # existing 顺序与 persisted 不同
        existing = [
            {'id': 'tc-3', 'name': 'third_tool', 'approval': {'state': 'approved'}},
            {'id': 'tc-1', 'name': 'shell_exec', 'approval': {'state': 'rejected'}},
            {'id': 'tc-2', 'name': 'file_read', 'approval': {'state': 'approved'}},
        ]

        merge_existing_approval_fields(persisted, existing)

        # 按 tool_call_id 匹配，不按位置
        self.assertEqual(persisted[0]['approval'], {'state': 'rejected'})  # tc-1
        self.assertEqual(persisted[1]['approval'], {'state': 'approved'})  # tc-2
        self.assertEqual(persisted[2]['approval'], {'state': 'approved'})  # tc-3

    def test_persisted_already_has_approval_not_overwritten(self):
        """persisted 已有 approval 字段 → 不被 existing 覆盖。"""
        persisted = [
            {
                'id': 'tc-1',
                'name': 'shell_exec',
                'args': {},
                'approval': {'state': 'approved', 'source': 'persisted'},
            },
        ]
        existing = [
            {'id': 'tc-1', 'name': 'shell_exec', 'approval': {'state': 'rejected', 'source': 'existing'}},
        ]

        merge_existing_approval_fields(persisted, existing)

        # 已有 approval 不被覆盖
        self.assertEqual(persisted[0]['approval'], {'state': 'approved', 'source': 'persisted'})

    def test_existing_tool_without_approval_not_indexed(self):
        """existing 工具无 approval 字段 → 不进入索引，不合并。"""
        persisted = [
            {'id': 'tc-1', 'name': 'shell_exec', 'args': {}},
        ]
        existing = [
            {'id': 'tc-1', 'name': 'shell_exec'},  # 无 approval 字段
        ]

        merge_existing_approval_fields(persisted, existing)

        self.assertNotIn('approval', persisted[0])

    def test_empty_lists_no_op(self):
        """空列表 → 无操作。"""
        persisted = []
        existing = []

        merge_existing_approval_fields(persisted, existing)

        self.assertEqual(persisted, [])
        self.assertEqual(existing, [])

    def test_persisted_empty_skips_merge(self):
        """persisted 为空 → 无操作。"""
        persisted = []
        existing = [
            {'id': 'tc-1', 'name': 'shell_exec', 'approval': {'state': 'approved'}},
        ]

        merge_existing_approval_fields(persisted, existing)

        self.assertEqual(persisted, [])

    def test_existing_empty_skips_merge(self):
        """existing 为空 → 无操作。"""
        persisted = [
            {'id': 'tc-1', 'name': 'shell_exec', 'args': {}},
        ]
        existing = []

        merge_existing_approval_fields(persisted, existing)

        self.assertNotIn('approval', persisted[0])

    def test_tool_call_id_field_takes_precedence_over_id(self):
        """tool_call_id 字段优先于 id 字段作为索引键。

    merge_existing_approval_fields 同时支持 tool_call_id 和 id 字段，
    tool_call_id 优先（与 build_persisted_tool_calls / approval payload 一致）。
    """
        persisted = [
            {'tool_call_id': 'real-tc-1', 'id': 'old-id-1', 'name': 'shell_exec', 'args': {}},
        ]
        existing = [
            {
                'tool_call_id': 'real-tc-1',
                'id': 'old-id-1',
                'name': 'shell_exec',
                'approval': {'state': 'approved'},
            },
        ]

        merge_existing_approval_fields(persisted, existing)

        self.assertEqual(persisted[0]['approval'], {'state': 'approved'})

    def test_match_by_id_field_when_tool_call_id_missing(self):
        """tool_call_id 字段缺失时降级使用 id 字段匹配。"""
        persisted = [
            {'id': 'tc-1', 'name': 'shell_exec', 'args': {}},
        ]
        existing = [
            {'id': 'tc-1', 'name': 'shell_exec', 'approval': {'state': 'approved'}},
        ]

        merge_existing_approval_fields(persisted, existing)

        self.assertEqual(persisted[0]['approval'], {'state': 'approved'})

    def test_non_dict_persisted_returns_no_op(self):
        """persisted_tool_calls 非 list → 无操作。"""
        persisted = 'not a list'
        existing = [
            {'id': 'tc-1', 'name': 'shell_exec', 'approval': {'state': 'approved'}},
        ]

        # 不应抛出异常
        merge_existing_approval_fields(persisted, existing)

        self.assertEqual(persisted, 'not a list')

    def test_non_dict_existing_returns_no_op(self):
        """existing_tool_calls 非 list → 无操作。"""
        persisted = [
            {'id': 'tc-1', 'name': 'shell_exec', 'args': {}},
        ]
        existing = 'not a list'

        # 不应抛出异常
        merge_existing_approval_fields(persisted, existing)

        self.assertNotIn('approval', persisted[0])


if __name__ == '__main__':
    unittest.main()
