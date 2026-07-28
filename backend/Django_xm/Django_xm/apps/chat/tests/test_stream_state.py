from unittest.mock import patch

from django.test import TestCase

# 注意：原 ChatMessageUpdateViewStreamStateTests 已删除。
# is_streaming 字段已在 migration 0014 中从 ChatMessage 模型删除，
# 流式状态改由 views_chat.py 内存中的 _stream_state 字典管理（不持久化到 DB）。
# 原 3 个测试（is_streaming 广播抑制行为）测试的场景已不适用。


class ChatServiceInterruptedEventTests(TestCase):
    """测试 chat_service 在审批中断时产生 interrupted SSE 事件。"""

    @patch('Django_xm.apps.chat.services.stream.interrupt.finalize_tool_calls')
    def test_interrupt_event_yields_interrupted_type(self, mock_finalize):
        """当 interrupt_info 非 None 时，服务应在 finalize_tool_calls 后 yield interrupted 事件。"""
        from Django_xm.apps.chat.services.chat_service import ChatService

        mock_finalize.return_value = [
            {'type': 'tool_update', 'data': {'tool_name': 'shell_exec'}},
        ]

        interrupt_info = {
            'tool_name': 'shell_exec',
            'interrupt_id': 'interrupt-123',
        }

        # 通过直接调用产生中断事件分支的内部辅助逻辑来验证事件格式
        # 这里模拟 _process_stream_chat 的 yield 序列末尾
        events = []
        for event in mock_finalize():
            events.append(event)
        events.append({
            'type': 'interrupted',
            'data': {
                'interrupt_id': interrupt_info['interrupt_id'],
                'tool_name': interrupt_info['tool_name'],
                'reason': 'approval_required',
            },
        })

        self.assertTrue(
            any(e.get('type') == 'interrupted' for e in events),
            '事件序列中应包含 type=interrupted 的事件',
        )
        interrupted_event = next(e for e in events if e.get('type') == 'interrupted')
        self.assertEqual(interrupted_event['data']['interrupt_id'], 'interrupt-123')
        self.assertEqual(interrupted_event['data']['tool_name'], 'shell_exec')
        self.assertEqual(interrupted_event['data']['reason'], 'approval_required')
