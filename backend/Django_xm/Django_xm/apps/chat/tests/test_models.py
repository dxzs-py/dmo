"""
聊天应用模型测试
"""
from django.test import TestCase
from ..models import ChatSession, ChatMessage


class ChatSessionModelTests(TestCase):
    """聊天会话模型测试"""

    def test_create_chat_session(self):
        """测试创建聊天会话"""
        session = ChatSession.objects.create(
            session_id='test-session-001'
        )
        
        self.assertEqual(session.session_id, 'test-session-001')
        self.assertIsNotNone(session.created_at)
        self.assertIsNotNone(session.updated_at)

    def test_chat_session_str(self):
        """测试ChatSession的__str__方法"""
        session = ChatSession.objects.create(
            session_id='test-session-002'
        )
        
        self.assertEqual(str(session), 'ChatSession test-session-002')


class ChatMessageModelTests(TestCase):
    """聊天消息模型测试"""

    def setUp(self):
        """测试前准备"""
        self.session = ChatSession.objects.create(
            session_id='test-session-003'
        )

    def test_create_chat_message(self):
        """测试创建聊天消息"""
        message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.ROLE_USER,
            content='Hello, how are you?'
        )
        
        self.assertEqual(message.session, self.session)
        self.assertEqual(message.role, ChatMessage.ROLE_USER)
        self.assertEqual(message.content, 'Hello, how are you?')

    def test_chat_message_str(self):
        """测试ChatMessage的__str__方法"""
        message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.ROLE_ASSISTANT,
            content='I am fine, thank you.'
        )
        
        expected_str = 'assistant: I am fine, thank you.'
        self.assertEqual(str(message), expected_str)

    def test_chat_message_long_content_str(self):
        """测试长内容的__str__方法截断"""
        long_content = 'This is a very long message that should be truncated in the string representation.'
        message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.ROLE_USER,
            content=long_content
        )
        
        self.assertTrue(len(str(message)) <= 60)

    def test_chat_message_role_choices(self):
        """测试角色选项"""
        message = ChatMessage.objects.create(
            session=self.session,
            role=ChatMessage.ROLE_SYSTEM,
            content='System message'
        )
        
        self.assertEqual(message.role, ChatMessage.ROLE_SYSTEM)
