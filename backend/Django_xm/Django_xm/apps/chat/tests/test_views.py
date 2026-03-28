"""
聊天视图测试
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class ChatViewTests(TestCase):
    """聊天视图测试"""

    def setUp(self):
        """测试前准备"""
        self.client = APIClient()

    def test_chat_view_get_not_allowed(self):
        """测试聊天视图不支持GET请求"""
        url = reverse('chat:chat')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.agents.create_base_agent')
    def test_chat_view_success(self, mock_create_agent):
        """测试成功发送聊天消息"""
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = "你好！我是AI助手。"
        mock_create_agent.return_value = mock_agent

        url = reverse('chat:chat')
        data = {
            'message': '你好',
            'mode': 'default',
            'chat_history': []
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('response', response.data)
        mock_agent.invoke.assert_called_once()

    def test_chat_view_missing_message(self):
        """测试缺少message参数"""
        url = reverse('chat:chat')
        data = {'mode': 'default'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('message', response.data)

    @patch('Django_xm.apps.agents.create_base_agent')
    def test_chat_view_with_history(self, mock_create_agent):
        """测试带聊天历史的请求"""
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = "基于历史，我记得上次我们在聊..."
        mock_create_agent.return_value = mock_agent

        url = reverse('chat:chat')
        data = {
            'message': '继续',
            'mode': 'default',
            'chat_history': [
                {'role': 'user', 'content': '你好'},
                {'role': 'assistant', 'content': '你好！有什么可以帮你的？'}
            ]
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_agent.invoke.assert_called_once()

    @patch('Django_xm.apps.agents.create_base_agent')
    def test_chat_view_server_error(self, mock_create_agent):
        """测试服务器错误情况"""
        mock_create_agent.side_effect = Exception('内部错误')

        url = reverse('chat:chat')
        data = {'message': '你好'}
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_chat_modes_view(self):
        """测试获取聊天模式列表"""
        url = reverse('chat:modes')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('modes', response.data)
        self.assertIsInstance(response.data['modes'], list)


class ChatStreamViewTests(TestCase):
    """流式聊天视图测试"""

    def setUp(self):
        """测试前准备"""
        self.client = APIClient()

    def test_chat_stream_view_get_not_allowed(self):
        """测试流式聊天视图不支持GET请求"""
        url = reverse('chat:stream')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.agents.create_base_agent')
    def test_chat_stream_view_success(self, mock_create_agent):
        """测试成功发送流式聊天消息"""
        mock_agent = MagicMock()
        mock_agent.stream.return_value = iter(['你', '好', '！'])
        mock_create_agent.return_value = mock_agent

        url = reverse('chat:stream')
        data = {
            'message': '你好',
            'mode': 'default'
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/event-stream')

    def test_chat_stream_view_missing_message(self):
        """测试流式聊天缺少message参数"""
        url = reverse('chat:stream')
        data = {'mode': 'default'}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
