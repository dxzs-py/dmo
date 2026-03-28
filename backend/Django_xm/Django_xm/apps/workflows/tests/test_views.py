"""
工作流视图测试
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class WorkflowViewTests(TestCase):
    """工作流视图测试"""

    def setUp(self):
        """测试前准备"""
        self.client = APIClient()
        self.test_thread_id = 'test_thread_123'

    def test_workflow_start_view_get_not_allowed(self):
        """测试工作流启动视图不支持GET请求"""
        url = reverse('workflows:start')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_start_view_success(self, mock_service):
        """测试成功启动工作流"""
        mock_service.start_workflow.return_value = {
            'thread_id': self.test_thread_id,
            'status': 'waiting_for_answers',
            'current_step': 'waiting_for_answers',
            'learning_plan': {'goal': '学习Python'},
            'quiz': {'questions': []},
            'message': '学习计划和练习题已生成'
        }

        url = reverse('workflows:start')
        data = {'user_question': '我想学习Python编程'}
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('thread_id', response.data)
        self.assertIn('status', response.data)
        mock_service.start_workflow.assert_called_once()

    def test_workflow_start_view_post_missing_data(self):
        """测试工作流启动视图缺少必填数据"""
        url = reverse('workflows:start')
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user_question', response.data)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_start_view_with_custom_thread_id(self, mock_service):
        """测试使用自定义thread_id启动工作流"""
        custom_thread_id = 'custom_thread_456'
        mock_service.start_workflow.return_value = {
            'thread_id': custom_thread_id,
            'status': 'waiting_for_answers',
            'message': '学习计划和练习题已生成'
        }

        url = reverse('workflows:start')
        data = {
            'user_question': '我想学习Python编程',
            'thread_id': custom_thread_id
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['thread_id'], custom_thread_id)
        mock_service.start_workflow.assert_called_once_with(
            user_question='我想学习Python编程',
            thread_id=custom_thread_id
        )

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_start_view_server_error(self, mock_service):
        """测试工作流启动时的服务器错误"""
        mock_service.start_workflow.side_effect = Exception('内部错误')

        url = reverse('workflows:start')
        data = {'user_question': '测试问题'}
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn('error', response.data)

    def test_workflow_submit_view_get_not_allowed(self):
        """测试提交答案视图不支持GET请求"""
        url = reverse('workflows:submit')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_workflow_submit_view_missing_data(self):
        """测试提交答案视图缺少必填数据"""
        url = reverse('workflows:submit')
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('thread_id', response.data)
        self.assertIn('answers', response.data)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_submit_view_success_completed(self, mock_service):
        """测试成功提交答案并完成测验"""
        mock_service.submit_user_answers.return_value = {
            'thread_id': self.test_thread_id,
            'status': 'completed',
            'current_step': 'end',
            'score': 85,
            'score_details': {'q1': '正确', 'q2': '正确'},
            'feedback': '做得很好！',
            'should_retry': False,
            'message': '恭喜通过测验！'
        }

        url = reverse('workflows:submit')
        data = {
            'thread_id': self.test_thread_id,
            'answers': {'q1': 'A', 'q2': 'B'}
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'completed')
        self.assertEqual(response.data['score'], 85)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_submit_view_retry(self, mock_service):
        """测试提交答案后需要重试"""
        mock_service.submit_user_answers.return_value = {
            'thread_id': self.test_thread_id,
            'status': 'retry',
            'current_step': 'quiz_generator',
            'score': 40,
            'should_retry': True,
            'message': '得分未达标，已重新生成练习题，请继续答题。'
        }

        url = reverse('workflows:submit')
        data = {
            'thread_id': self.test_thread_id,
            'answers': {'q1': '错误答案'}
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'retry')
        self.assertTrue(response.data['should_retry'])

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_submit_view_failed(self, mock_service):
        """测试提交答案后达到最大重试次数"""
        mock_service.submit_user_answers.return_value = {
            'thread_id': self.test_thread_id,
            'status': 'failed',
            'current_step': 'end',
            'score': 30,
            'should_retry': False,
            'message': '已达到最大重试次数，建议复习后再来挑战。'
        }

        url = reverse('workflows:submit')
        data = {
            'thread_id': self.test_thread_id,
            'answers': {'q1': '错误答案'}
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'failed')

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_submit_view_server_error(self, mock_service):
        """测试提交答案时的服务器错误"""
        mock_service.submit_user_answers.side_effect = Exception('处理失败')

        url = reverse('workflows:submit')
        data = {
            'thread_id': self.test_thread_id,
            'answers': {'q1': 'A'}
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_workflow_status_view_post_not_allowed(self):
        """测试状态查询视图不支持POST请求"""
        url = reverse('workflows:status', kwargs={'thread_id': self.test_thread_id})
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_status_view_success(self, mock_service):
        """测试成功查询工作流状态"""
        mock_service.get_workflow_status.return_value = {
            'thread_id': self.test_thread_id,
            'current_step': 'waiting_for_answers',
            'user_question': '学习Python',
            'quiz': {'questions': []}
        }

        url = reverse('workflows:status', kwargs={'thread_id': self.test_thread_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('thread_id', response.data)
        self.assertIn('state', response.data)
        self.assertEqual(response.data['thread_id'], self.test_thread_id)

    def test_workflow_status_view_invalid_thread_id(self):
        """测试查询不存在的工作流状态"""
        url = reverse('workflows:status', kwargs={'thread_id': 'non-existent-thread'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_status_view_server_error(self, mock_service):
        """测试状态查询时的服务器错误"""
        mock_service.get_workflow_status.side_effect = Exception('查询失败')

        url = reverse('workflows:status', kwargs={'thread_id': self.test_thread_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_workflow_history_view_post_not_allowed(self):
        """测试历史查询视图不支持POST请求"""
        url = reverse('workflows:history', kwargs={'thread_id': self.test_thread_id})
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_history_view_success(self, mock_service):
        """测试成功查询工作流历史"""
        mock_service.get_workflow_history.return_value = [
            {'step': 'planner', 'timestamp': '2024-01-01T00:00:00'},
            {'step': 'quiz_generator', 'timestamp': '2024-01-01T00:00:01'}
        ]

        url = reverse('workflows:history', kwargs={'thread_id': self.test_thread_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('thread_id', response.data)
        self.assertIn('history', response.data)
        self.assertEqual(len(response.data['history']), 2)

    def test_workflow_history_view_invalid_thread_id(self):
        """测试查询不存在的工作流历史"""
        url = reverse('workflows:history', kwargs={'thread_id': 'non-existent-thread'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_history_view_server_error(self, mock_service):
        """测试历史查询时的服务器错误"""
        mock_service.get_workflow_history.side_effect = Exception('查询失败')

        url = reverse('workflows:history', kwargs={'thread_id': self.test_thread_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    @patch('Django_xm.apps.workflows.views.get_workflow_state')
    @patch('Django_xm.apps.workflows.views._get_study_flow')
    def test_workflow_stream_view_success(self, mock_get_flow, mock_get_state):
        """测试流式获取工作流进度"""
        mock_get_state.return_value = {
            'thread_id': self.test_thread_id,
            'current_step': 'waiting_for_answers'
        }

        mock_flow = MagicMock()
        mock_flow.graph.stream.return_value = [
            {'current_step': 'grading'},
            {'current_step': 'feedback'}
        ]
        mock_get_flow.return_value = mock_flow

        url = reverse('workflows:stream', kwargs={'thread_id': self.test_thread_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/event-stream')

    def test_workflow_delete_view_get_not_allowed(self):
        """测试删除工作流视图不支持GET请求"""
        url = reverse('workflows:delete', kwargs={'thread_id': 'test-thread'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_workflow_delete_view_post_not_allowed(self):
        """测试删除工作流视图不支持POST请求"""
        url = reverse('workflows:delete', kwargs={'thread_id': 'test-thread'})
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_delete_view_success(self, mock_service):
        """测试成功删除工作流"""
        mock_service.delete_workflow.return_value = {
            'thread_id': self.test_thread_id,
            'status': 'deleted',
            'message': '工作流已删除（注意：检查点数据可能仍然存在）'
        }

        url = reverse('workflows:delete', kwargs={'thread_id': self.test_thread_id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'deleted')
        mock_service.delete_workflow.assert_called_once_with(self.test_thread_id)

    @patch('Django_xm.apps.workflows.views.WorkflowService')
    def test_workflow_delete_view_server_error(self, mock_service):
        """测试删除工作流时的服务器错误"""
        mock_service.delete_workflow.side_effect = Exception('删除失败')

        url = reverse('workflows:delete', kwargs={'thread_id': self.test_thread_id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
