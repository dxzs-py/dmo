"""
聊天服务层测试
测试 ChatService 和 ChatModeService 的业务逻辑
"""
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
from django.test import TestCase


class ChatServiceTest(TestCase):
    """聊天服务类测试"""

    def setUp(self):
        self.test_data = {
            'message': '你好',
            'mode': 'default',
            'chat_history': [],
            'use_tools': True,
            'use_advanced_tools': False,
        }

    @patch('Django_xm.apps.chat.services.chat_service.get_chat_model')
    @patch('Django_xm.apps.chat.services.chat_service.create_base_agent')
    def test_process_chat_request_success(self, mock_create_agent, mock_get_model):
        """测试成功处理聊天请求"""
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = "你好！我是AI助手。"
        mock_create_agent.return_value = mock_agent

        from Django_xm.apps.chat.services.chat_service import ChatService
        
        result = ChatService.process_chat_request(self.test_data)

        self.assertTrue(result['success'])
        self.assertIn('message', result)
        self.assertIn('tools_used', result)
        self.assertEqual(result['mode'], 'default')
        mock_agent.invoke.assert_called_once()

    @patch('Django_xm.apps.chat.services.chat_service.get_chat_model')
    @patch('Django_xm.apps.chat.services.chat_service.create_base_agent')
    def test_process_chat_request_with_completion(self, mock_create_agent, mock_get_model):
        """测试需要补全的聊天请求"""
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = "不完整的回答"
        mock_create_agent.return_value = mock_agent
        
        mock_model = MagicMock()
        mock_completion = MagicMock()
        mock_completion.content = "完整的回答内容"
        mock_model.invoke.return_value = mock_completion
        mock_get_model.return_value = mock_model

        with patch('Django_xm.apps.chat.services.chat_service._needs_completion', return_value=True):
            from Django_xm.apps.chat.services.chat_service import ChatService
            
            result = ChatService.process_chat_request(self.test_data)
            
            self.assertTrue(result['success'])
            mock_model.invoke.assert_called_once()

    @patch('Django_xm.apps.chat.services.chat_service.should_use_deep_research')
    @patch('Django_xm.apps.chat.services.chat_service.create_deep_research_agent')
    async def test_process_stream_chat_with_deep_research(self, mock_create_agent, mock_should_use):
        """测试流式聊天触发深度研究"""
        mock_should_use.return_value = True
        mock_agent = MagicMock()
        mock_agent.research.return_value = {
            'final_report': '深度研究报告'
        }
        mock_create_agent.return_value = mock_agent

        from Django_xm.apps.chat.services.chat_service import ChatService
        
        events = []
        async for event in ChatService.process_stream_chat_request(self.test_data):
            events.append(event)

        self.assertTrue(len(events) > 0)
        self.assertEqual(events[0]['type'], 'start')

    @patch('Django_xm.apps.chat.services.chat_service.should_use_deep_research')
    async def test_process_stream_chat_normal(self, mock_should_use):
        """测试普通流式聊天"""
        mock_should_use.return_value = False

        from Django_xm.apps.chat.services.chat_service import ChatService
        
        events = []
        async for event in ChatService.process_stream_chat_request(self.test_data):
            events.append(event)
            if event['type'] == 'end':
                break

        self.assertTrue(len(events) > 0)
        self.assertEqual(events[0]['type'], 'start')


class ChatModeServiceTest(TestCase):
    """聊天模式服务类测试"""

    def test_get_supported_modes(self):
        """测试获取支持的模式列表"""
        from Django_xm.apps.chat.services.chat_service import ChatModeService
        
        modes = ChatModeService.get_supported_modes()
        
        self.assertIsInstance(modes, dict)
        self.assertTrue(len(modes) > 0)
        
        for mode_name in ChatModeService.FRONTEND_SUPPORTED_MODES:
            if mode_name in modes:
                self.assertIsInstance(modes[mode_name], str)

    def test_frontend_supported_modes(self):
        """测试前端支持的模式"""
        from Django_xm.apps.chat.services.chat_service import ChatModeService
        
        expected_modes = ['basic-agent', 'rag', 'workflow', 'deep-research', 'guarded']
        self.assertEqual(ChatModeService.FRONTEND_SUPPORTED_MODES, expected_modes)


class ServiceIntegrationTest(TestCase):
    """服务集成测试"""

    @patch('Django_xm.apps.chat.services.chat_service.create_base_agent')
    def test_chat_service_integration(self, mock_create_agent):
        """测试聊天服务集成"""
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = "集成测试响应"
        mock_create_agent.return_value = mock_agent

        from Django_xm.apps.chat.services.chat_service import ChatService, ChatModeService
        
        data = {
            'message': '测试消息',
            'mode': 'basic-agent',
            'chat_history': [],
            'use_tools': True,
            'use_advanced_tools': False,
        }

        result = ChatService.process_chat_request(data)
        modes = ChatModeService.get_supported_modes()

        self.assertTrue(result['success'])
        self.assertIn('basic-agent', modes)
