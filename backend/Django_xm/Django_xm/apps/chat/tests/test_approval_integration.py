"""审批集成测试。

测试 ChatStreamView 审批事件透传、ChatApprovalView 委托 approval_service、
chat_resume_service 流式输出与审批完成回调。

覆盖：
1. ChatStreamView 检测 approval 事件时透传 source/source_id/chat_session_id
2. ChatApprovalView.post 委托 approval_service.resume_approval 并调用 stream_chat_resume_response
3. chat_resume_service.stream_chat_resume_generator 产出 SSE 事件（start、chunk、end、[DONE]）
4. chat_resume_service 流结束后调用 complete_approval_async(state='approved')
"""

import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from asgiref.sync import async_to_sync
from django.http import HttpResponse
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from Django_xm.apps.users.models import User
from Django_xm.apps.chat.models import ChatSession, ChatMessage, MessageRole
from Django_xm.apps.approvals.models import Approval


def _consume_async_generator(gen):
    """同步消费 async generator，触发其内部副作用。"""
    async def _consume():
        async for _ in gen:
            pass
    async_to_sync(_consume)()


# ==================== 辅助函数 ====================

def _make_mock_request(data=None):
    """构造 mock request，用于直接调用 stream_chat_resume_generator。"""
    mock_request = MagicMock()
    mock_request.user.id = 1
    mock_request.data = data or {
        'use_tools': True,
        'use_web_search': False,
        'use_mcp': False,
        'selected_mcp_servers': None,
        'selected_tools': None,
        'use_knowledge_base': False,
        'selected_knowledge_bases': [],
        'provider_id': None,
        'model_name': None,
        'use_deep_thinking': False,
        'special_params': None,
        'temperature': None,
        'max_tokens': None,
        'mode': 'agent',
    }
    return mock_request


def _setup_mock_chat_service(mock_chat_service_class, mock_agent=None):
    """配置 ChatService mock，返回 (mock_service, mock_agent)。"""
    mock_service = MagicMock()
    mock_chat_service_class.return_value = mock_service
    mock_chat_service_class._resolve_model_instance.return_value = MagicMock()
    mock_service._build_tool_config.return_value = {'use_tools': True}

    async def mock_get_tools(data):
        return []
    mock_service._get_tools = mock_get_tools

    if mock_agent is None:
        mock_agent = MagicMock()
        mock_graph = MagicMock()

        async def mock_aget_state(config):
            mock_state = MagicMock()
            mock_state.tasks = []
            mock_state.values = {"messages": []}
            return mock_state

        mock_graph.aget_state = mock_aget_state
        mock_agent.graph = mock_graph

    async def mock_create_agent(data, prompt_mode='agent', model_instance=None,
                                tool_config=None, tools=None):
        return (mock_agent, {"configurable": {"thread_id": "test-session"}}, True)
    mock_service._create_agent_with_memory = mock_create_agent

    return mock_service, mock_agent


# ==================== 测试 1: ChatStreamView 审批事件透传 ====================

class ChatStreamViewApprovalPassthroughTests(TransactionTestCase):
    """测试 ChatStreamView 检测 approval 事件时透传 source/source_id/chat_session_id。"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='approval_passthrough_user',
            password='testpass123',
        )
        self.client.force_authenticate(user=self.user)
        self.session = ChatSession.objects.create(
            user=self.user, title='Approval Passthrough Session',
        )

    @patch('Django_xm.apps.chat.views_chat.approval_service')
    @patch('Django_xm.apps.chat.views_chat.publish_event', new_callable=AsyncMock)
    @patch('Django_xm.apps.chat.views_chat.publish_event_sync')
    @patch('Django_xm.apps.chat.views_chat.ChatService')
    def test_approval_event_passthrough(
        self,
        mock_chat_service_class,
        mock_publish,
        mock_publish_async,
        mock_approval_service,
    ):
        """ChatStreamView 检测到 approval 事件时调用 approval_service.request_approval_async 透传参数。"""
        approval_event = {
            'type': 'approval',
            'data': {
                'interrupt_id': 'test-interrupt-123',
                'tool_name': 'shell_exec',
                'title': '确认执行',
                'description': '执行命令',
                'action': 'confirm',
                'operation': 'rm -rf /tmp/test',
                'danger_level': 'high',
                'parameters': {'command': 'rm -rf /tmp/test'},
                'extra': {},
            },
        }

        async def mock_process_stream(data):
            yield approval_event

        mock_service = MagicMock()
        mock_service.process_stream_chat_request = mock_process_stream
        mock_chat_service_class.return_value = mock_service

        mock_approval_service.request_approval_async = AsyncMock()

        response = self.client.post(
            reverse('chat:chat-stream'),
            data={
                'message': 'test message',
                'session_id': self.session.session_id,
                'mode': 'agent',
            },
            format='json',
        )

        # 消费流式响应以触发 generate() 内部的 approval 透传逻辑
        _consume_async_generator(response.streaming_content)

        mock_approval_service.request_approval_async.assert_called_once()
        kwargs = mock_approval_service.request_approval_async.call_args.kwargs
        self.assertEqual(kwargs['source'], Approval.SOURCE_CHAT)
        self.assertEqual(kwargs['source_id'], self.session.session_id)
        self.assertEqual(kwargs['approval_data'].get('session_id'), self.session.session_id)
        self.assertEqual(kwargs['interrupt_id'], 'test-interrupt-123')

    @patch('Django_xm.apps.chat.views_chat.approval_service')
    @patch('Django_xm.apps.chat.views_chat.publish_event', new_callable=AsyncMock)
    @patch('Django_xm.apps.chat.views_chat.publish_event_sync')
    @patch('Django_xm.apps.chat.views_chat.ChatService')
    def test_deep_research_approval_uses_task_id_as_source_id(
        self,
        mock_chat_service_class,
        mock_publish,
        mock_publish_async,
        mock_approval_service,
    ):
        """source=deep_research 且带 task_id 时，source_id 应为 task_id 而非 session_id。"""
        approval_event = {
            'type': 'approval',
            'data': {
                'interrupt_id': 'dr-interrupt-456',
                'source': Approval.SOURCE_DEEP_RESEARCH,
                'task_id': 'dr-task-789',
                'tool_name': 'shell_exec',
                'title': '确认',
                'description': '',
                'action': 'confirm',
                'danger_level': 'high',
                'parameters': {},
                'extra': {},
            },
        }

        async def mock_process_stream(data):
            yield approval_event

        mock_service = MagicMock()
        mock_service.process_stream_chat_request = mock_process_stream
        mock_chat_service_class.return_value = mock_service

        mock_approval_service.request_approval_async = AsyncMock()

        response = self.client.post(
            reverse('chat:chat-stream'),
            data={
                'message': 'test',
                'session_id': self.session.session_id,
                'mode': 'agent',
            },
            format='json',
        )

        _consume_async_generator(response.streaming_content)

        mock_approval_service.request_approval_async.assert_called_once()
        kwargs = mock_approval_service.request_approval_async.call_args.kwargs
        self.assertEqual(kwargs['source'], Approval.SOURCE_DEEP_RESEARCH)
        self.assertEqual(kwargs['source_id'], 'dr-task-789')
        self.assertEqual(kwargs['approval_data'].get('session_id'), self.session.session_id)


# ==================== 测试 2: ChatApprovalView 委托 approval_service ====================

class ChatApprovalViewDelegationTests(TransactionTestCase):
    """测试 ChatApprovalView.post 委托 approval_service.resume_approval。"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='approval_delegate_user',
            password='testpass123',
        )
        self.client.force_authenticate(user=self.user)
        self.session = ChatSession.objects.create(
            user=self.user, title='Delegate Session',
        )

    @unittest.skip('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')
    @patch('Django_xm.apps.chat.services.chat_resume_service.stream_chat_resume_response')
    @patch('Django_xm.apps.chat.views_chat.approval_service')
    def test_post_delegates_to_approval_service_and_stream_resume(
        self,
        mock_approval_service,
        mock_stream_resume,
    ):
        """ChatApprovalView.post 委托 approval_service.resume_approval 并调用 stream_chat_resume_response。"""
        mock_approval = MagicMock()
        mock_approval.source = Approval.SOURCE_CHAT
        mock_approval.interrupt_id = 'test-interrupt-123'
        mock_approval_service.resume_approval.return_value = {
            'approval': mock_approval,
            'resume_value': True,
            'stream_generator': None,
        }

        mock_response = HttpResponse('test', status=200)
        mock_stream_resume.return_value = mock_response

        response = self.client.post(
            reverse('chat:chat-approval'),
            data={
                'session_id': self.session.session_id,
                'interrupt_id': 'test-interrupt-123',
                'approved': True,
            },
            format='json',
        )

        # 验证 approval_service.resume_approval 被调用
        mock_approval_service.resume_approval.assert_called_once()
        call_kwargs = mock_approval_service.resume_approval.call_args.kwargs
        self.assertEqual(call_kwargs['interrupt_id'], 'test-interrupt-123')
        self.assertTrue(call_kwargs['approved'])
        self.assertIsNone(call_kwargs['user_input'])

        # 验证 stream_chat_resume_response 被调用
        mock_stream_resume.assert_called_once()
        args = mock_stream_resume.call_args.args
        self.assertIs(args[1], mock_approval)  # approval
        self.assertEqual(args[2], True)  # resume_value
        self.assertEqual(args[3], self.session.session_id)  # session_id
        self.assertEqual(args[4], True)  # approved

    @patch('Django_xm.apps.chat.views_chat.approval_service')
    def test_post_missing_session_id_returns_error(self, mock_approval_service):
        """缺少 session_id 时返回参数错误。"""
        response = self.client.post(
            reverse('chat:chat-approval'),
            data={'interrupt_id': 'test-interrupt-123', 'approved': True},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        mock_approval_service.resume_approval.assert_not_called()

    @patch('Django_xm.apps.chat.views_chat.approval_service')
    def test_post_invalid_session_returns_404(self, mock_approval_service):
        """会话不存在时返回 404。"""
        response = self.client.post(
            reverse('chat:chat-approval'),
            data={
                'session_id': 'nonexistent-session',
                'interrupt_id': 'test-interrupt-123',
                'approved': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 404)
        mock_approval_service.resume_approval.assert_not_called()


# ==================== 测试 3: stream_chat_resume_generator SSE 输出 ====================

@unittest.skip('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')
class StreamChatResumeGeneratorSSETests(TestCase):
    """测试 stream_chat_resume_generator 产出 SSE 事件。"""

    @patch('Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer',
           new_callable=AsyncMock)
    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('Django_xm.apps.chat.services.chat_resume_service.approval_service')
    @patch('Django_xm.apps.chat.services.stream_helpers.process_stream_chunk')
    @patch('Django_xm.apps.chat.services.chat_service.ChatService')
    async def test_generator_produces_start_chunk_end_done(
        self,
        mock_chat_service_class,
        mock_process_chunk,
        mock_approval_service,
        mock_sleep,
        mock_release,
    ):
        """测试生成器产出 start、chunk、end、[DONE] SSE 事件。"""
        import unittest
        raise unittest.SkipTest('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')

        # 构造 mock agent，astream 产出一个 messages 模式 chunk
        mock_agent = MagicMock()
        mock_graph = MagicMock()

        async def mock_astream(command, config=None, stream_mode=None, **kwargs):
            yield ("messages", (MagicMock(content="Hello"), {"langgraph_node": "agent"}))

        mock_graph.astream = mock_astream

        async def mock_aget_state(config):
            mock_state = MagicMock()
            mock_state.tasks = []
            mock_state.values = {"messages": []}
            return mock_state

        mock_graph.aget_state = mock_aget_state
        mock_agent.graph = mock_graph

        _setup_mock_chat_service(mock_chat_service_class, mock_agent=mock_agent)

        # mock process_stream_chunk 返回 chunk 事件
        mock_process_chunk.return_value = [{'type': 'chunk', 'content': 'Hello'}]

        mock_approval = MagicMock()
        mock_approval.interrupt_id = 'test-interrupt-123'
        mock_approval_service.complete_approval_async = AsyncMock()

        mock_request = _make_mock_request()

        generator = stream_chat_resume_generator(
            mock_request, mock_approval, True, 'test-session-id', True,
        )

        events = []
        async for event in generator:
            events.append(event)

        # 验证 SSE 事件格式
        event_text = ''.join(events)
        self.assertIn('"type": "start"', event_text)
        self.assertIn('"type": "chunk"', event_text)
        self.assertIn('"type": "end"', event_text)
        self.assertIn('[DONE]', event_text)

        # 验证事件顺序：start 在 chunk 之前，end 在 [DONE] 之前
        start_idx = event_text.find('"type": "start"')
        chunk_idx = event_text.find('"type": "chunk"')
        end_idx = event_text.find('"type": "end"')
        done_idx = event_text.find('[DONE]')
        self.assertLess(start_idx, chunk_idx)
        self.assertLess(chunk_idx, end_idx)
        self.assertLess(end_idx, done_idx)


# ==================== 测试 4: complete_approval_async 调用 ====================

@unittest.skip('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')
class StreamChatResumeCompleteApprovalTests(TestCase):
    """测试 stream_chat_resume_generator 流结束后调用 complete_approval_async。"""

    @patch('Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer',
           new_callable=AsyncMock)
    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('Django_xm.apps.chat.services.chat_resume_service.approval_service')
    @patch('Django_xm.apps.chat.services.stream_helpers.process_stream_chunk')
    @patch('Django_xm.apps.chat.services.chat_service.ChatService')
    async def test_natural_end_calls_complete_approval_approved(
        self,
        mock_chat_service_class,
        mock_process_chunk,
        mock_approval_service,
        mock_sleep,
        mock_release,
    ):
        """自然结束时调用 complete_approval_async(state='approved')。"""
        import unittest
        raise unittest.SkipTest('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')

        # 构造 mock agent，astream 不产出任何 chunk（自然结束）
        mock_agent = MagicMock()
        mock_graph = MagicMock()

        async def mock_astream(command, config=None, stream_mode=None, **kwargs):
            return
            yield  # 使函数成为 async generator

        mock_graph.astream = mock_astream

        async def mock_aget_state(config):
            mock_state = MagicMock()
            mock_state.tasks = []
            mock_state.values = {"messages": []}
            return mock_state

        mock_graph.aget_state = mock_aget_state
        mock_agent.graph = mock_graph

        _setup_mock_chat_service(mock_chat_service_class, mock_agent=mock_agent)

        mock_process_chunk.return_value = []

        mock_approval = MagicMock()
        mock_approval.interrupt_id = 'test-interrupt-123'
        mock_approval_service.complete_approval_async = AsyncMock()

        mock_request = _make_mock_request()

        generator = stream_chat_resume_generator(
            mock_request, mock_approval, True, 'test-session-id', True,
        )

        async for _ in generator:
            pass

        mock_approval_service.complete_approval_async.assert_called_once_with(
            'test-interrupt-123', Approval.STATE_APPROVED,
        )

    @patch('Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer',
           new_callable=AsyncMock)
    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('Django_xm.apps.tools.base.is_approval_interrupt', return_value=True)
    @patch('Django_xm.apps.chat.services.chat_resume_service.approval_service')
    @patch('Django_xm.apps.chat.services.stream_helpers.process_stream_chunk')
    @patch('Django_xm.apps.chat.services.chat_service.ChatService')
    async def test_ended_by_interrupt_calls_complete_approval_approved(
        self,
        mock_chat_service_class,
        mock_process_chunk,
        mock_approval_service,
        mock_is_approval_interrupt,
        mock_sleep,
        mock_release,
    ):
        """ended_by_interrupt 时调用 complete_approval_async(state='approved')。"""
        import unittest
        raise unittest.SkipTest('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')

        # 构造 mock agent，astream 产出 __interrupt__ 事件
        mock_agent = MagicMock()
        mock_graph = MagicMock()

        interrupt_value = {
            '_approval': True,
            'tool_name': 'shell_exec',
            'title': '确认执行',
            'description': '执行命令',
            'action': 'confirm',
            'operation': 'rm -rf /tmp/test',
            'danger_level': 'high',
        }
        interrupt_dict = {"value": interrupt_value, "id": "new-interrupt-id"}

        async def mock_astream(command, config=None, stream_mode=None, **kwargs):
            yield ("updates", {"__interrupt__": [interrupt_dict]})

        mock_graph.astream = mock_astream

        async def mock_aget_state(config):
            mock_state = MagicMock()
            mock_state.tasks = []
            mock_state.values = {"messages": []}
            return mock_state

        mock_graph.aget_state = mock_aget_state
        mock_agent.graph = mock_graph

        _setup_mock_chat_service(mock_chat_service_class, mock_agent=mock_agent)

        mock_process_chunk.return_value = []

        mock_approval = MagicMock()
        mock_approval.interrupt_id = 'test-interrupt-123'
        mock_approval_service.complete_approval_async = AsyncMock()
        mock_approval_service.request_approval_async = AsyncMock()

        mock_request = _make_mock_request()

        generator = stream_chat_resume_generator(
            mock_request, mock_approval, True, 'test-session-id', True,
        )

        events = []
        async for event in generator:
            events.append(event)

        # 验证 ended_by_interrupt 路径也调用 complete_approval_async(state='approved')
        mock_approval_service.complete_approval_async.assert_called_once_with(
            'test-interrupt-123', Approval.STATE_APPROVED,
        )

        # 验证流中包含 approval 事件（新审批）
        event_text = ''.join(events)
        self.assertIn('"type": "approval"', event_text)

    @patch('Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer',
           new_callable=AsyncMock)
    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('Django_xm.apps.chat.services.chat_resume_service.approval_service')
    @patch('Django_xm.apps.chat.services.stream_helpers.process_stream_chunk')
    @patch('Django_xm.apps.chat.services.chat_service.ChatService')
    async def test_rejected_approval_calls_complete_approval_rejected(
        self,
        mock_chat_service_class,
        mock_process_chunk,
        mock_approval_service,
        mock_sleep,
        mock_release,
    ):
        """approved=False 时调用 complete_approval_async(state='rejected')。"""
        import unittest
        raise unittest.SkipTest('chat_resume_service 已在 Phase 1 移除，相关测试待后续 Phase 重建')

        mock_agent = MagicMock()
        mock_graph = MagicMock()

        async def mock_astream(command, config=None, stream_mode=None, **kwargs):
            return
            yield

        mock_graph.astream = mock_astream

        async def mock_aget_state(config):
            mock_state = MagicMock()
            mock_state.tasks = []
            mock_state.values = {"messages": []}
            return mock_state

        mock_graph.aget_state = mock_aget_state
        mock_agent.graph = mock_graph

        _setup_mock_chat_service(mock_chat_service_class, mock_agent=mock_agent)

        mock_process_chunk.return_value = []

        mock_approval = MagicMock()
        mock_approval.interrupt_id = 'test-interrupt-123'
        mock_approval_service.complete_approval_async = AsyncMock()

        mock_request = _make_mock_request()

        generator = stream_chat_resume_generator(
            mock_request, mock_approval, False, 'test-session-id', False,
        )

        async for _ in generator:
            pass

        mock_approval_service.complete_approval_async.assert_called_once_with(
            'test-interrupt-123', Approval.STATE_REJECTED,
        )
