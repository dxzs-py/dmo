"""
工作流序列化器测试
"""
from django.test import TestCase
from ..serializers import (
    WorkflowStartSerializer,
    WorkflowSubmitSerializer,
    WorkflowStatusSerializer,
    WorkflowResponseSerializer
)


class WorkflowStartSerializerTests(TestCase):
    """启动工作流序列化器测试"""

    def test_valid_data(self):
        """测试有效数据"""
        data = {
            'user_question': '如何学习Python编程？',
            'thread_id': 'test-thread-001'
        }
        serializer = WorkflowStartSerializer(data=data)
        self.assertTrue(serializer.is_valid())

    def test_required_user_question(self):
        """测试user_question是必填项"""
        data = {}
        serializer = WorkflowStartSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('user_question', serializer.errors)

    def test_user_question_min_length(self):
        """测试user_question最小长度验证"""
        data = {'user_question': ''}
        serializer = WorkflowStartSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('user_question', serializer.errors)

    def test_thread_id_optional(self):
        """测试thread_id是可选的"""
        data = {'user_question': '如何学习Python编程？'}
        serializer = WorkflowStartSerializer(data=data)
        self.assertTrue(serializer.is_valid())


class WorkflowSubmitSerializerTests(TestCase):
    """提交答案序列化器测试"""

    def test_valid_data(self):
        """测试有效数据"""
        data = {
            'thread_id': 'test-thread-001',
            'answers': {
                'q1': '答案1',
                'q2': '答案2'
            }
        }
        serializer = WorkflowSubmitSerializer(data=data)
        self.assertTrue(serializer.is_valid())

    def test_required_fields(self):
        """测试必填字段"""
        data = {}
        serializer = WorkflowSubmitSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('thread_id', serializer.errors)
        self.assertIn('answers', serializer.errors)


class WorkflowStatusSerializerTests(TestCase):
    """工作流状态序列化器测试"""

    def test_serialization(self):
        """测试序列化"""
        data = {
            'thread_id': 'test-thread-001',
            'current_step': 'waiting_for_answers',
            'status': 'waiting_for_answers',
            'user_question': '如何学习Python编程？',
            'learning_plan': {'topic': 'Python'},
            'quiz': {'questions': []},
            'score': None,
            'should_retry': False
        }
        serializer = WorkflowStatusSerializer(data=data)
        self.assertTrue(serializer.is_valid())


class WorkflowResponseSerializerTests(TestCase):
    """工作流响应序列化器测试"""

    def test_serialization(self):
        """测试序列化"""
        data = {
            'thread_id': 'test-thread-001',
            'status': 'completed',
            'message': '学习计划和练习题已生成',
            'learning_plan': {'topic': 'Python'},
            'quiz': {'questions': []},
            'error': None
        }
        serializer = WorkflowResponseSerializer(data=data)
        self.assertTrue(serializer.is_valid())
