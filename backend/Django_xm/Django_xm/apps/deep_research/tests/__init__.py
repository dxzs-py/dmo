"""
Deep Research视图测试
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class DeepResearchViewTests(TestCase):
    """深度研究视图测试"""

    def setUp(self):
        """测试前准备"""
        self.client = APIClient()
        self.test_task_id = 'test_task_123'

    def test_start_view_get_not_allowed(self):
        """测试启动视图不支持GET请求"""
        url = reverse('deep_research:start')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.deep_research.views.create_deep_research_agent')
    def test_start_view_success(self, mock_create_agent):
        """测试成功启动研究任务"""
        mock_agent = MagicMock()
        mock_agent.research.return_value = {
            'final_report': '研究报告',
            'plan': {'research_goal': '测试'},
            'steps_completed': {'planning': True}
        }
        mock_create_agent.return_value = mock_agent

        url = reverse('deep_research:start')
        data = {
            'query': '分析LangChain的新特性',
            'enable_web_search': True,
            'enable_doc_analysis': False
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('thread_id', response.data)
        self.assertIn('status', response.data)

    def test_start_view_missing_query(self):
        """测试启动视图缺少query参数"""
        url = reverse('deep_research:start')
        data = {'enable_web_search': True}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('query', response.data)

    @patch('Django_xm.apps.deep_research.views.ResearchTask.objects.get')
    def test_status_view_success(self, mock_get_task):
        """测试成功查询任务状态"""
        mock_task = MagicMock()
        mock_task.task_id = self.test_task_id
        mock_task.status = 'running'
        mock_task.query = '测试查询'
        mock_task.get_status_display.return_value = '运行中'
        mock_get_task.return_value = mock_task

        url = reverse('deep_research:status', kwargs={'task_id': self.test_task_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('status', response.data)
        self.assertEqual(response.data['status'], 'running')

    @patch('Django_xm.apps.deep_research.views.ResearchTask.objects.get')
    def test_status_view_not_found(self, mock_get_task):
        """测试查询不存在的任务状态"""
        from django.core.exceptions import ObjectDoesNotExist
        mock_get_task.side_effect = ObjectDoesNotExist

        url = reverse('deep_research:status', kwargs={'task_id': 'non-existent'})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('Django_xm.apps.deep_research.views.ResearchTask.objects.get')
    def test_result_view_success(self, mock_get_task):
        """测试成功获取研究结果"""
        mock_task = MagicMock()
        mock_task.task_id = self.test_task_id
        mock_task.status = 'completed'
        mock_task.query = '测试查询'
        mock_task.final_report = '研究报告内容'
        mock_task.created_at.isoformat.return_value = '2024-01-01T00:00:00'
        mock_task.updated_at.isoformat.return_value = '2024-01-01T00:10:00'
        mock_get_task.return_value = mock_task

        url = reverse('deep_research:result', kwargs={'task_id': self.test_task_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('final_report', response.data)

    @patch('Django_xm.apps.deep_research.views.ResearchTask.objects.get')
    def test_result_view_not_completed(self, mock_get_task):
        """测试获取未完成任务的结果"""
        mock_task = MagicMock()
        mock_task.task_id = self.test_task_id
        mock_task.status = 'running'
        mock_task.query = '测试查询'
        mock_get_task.return_value = mock_task

        url = reverse('deep_research:result', kwargs={'task_id': self.test_task_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('error', response.data)

    @patch('Django_xm.apps.deep_research.views.ResearchTask.objects.get')
    def test_files_view_success(self, mock_get_task):
        """测试成功获取文件列表"""
        mock_task = MagicMock()
        mock_task.task_id = self.test_task_id
        mock_get_task.return_value = mock_task

        url = reverse('deep_research:files', kwargs={'task_id': self.test_task_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('files', response.data)

    @patch('Django_xm.apps.deep_research.views.ResearchTask.objects.get')
    @patch('Django_xm.apps.deep_research.views.get_filesystem')
    def test_file_download_view_success(self, mock_get_fs, mock_get_task):
        """测试成功下载文件"""
        mock_task = MagicMock()
        mock_task.task_id = self.test_task_id
        mock_get_task.return_value = mock_task

        mock_fs = MagicMock()
        mock_fs.read_file.return_value = '文件内容'
        mock_get_fs.return_value = mock_fs

        url = reverse('deep_research:file', kwargs={
            'task_id': self.test_task_id,
            'filename': 'test.md'
        })
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('content', response.data)

    @patch('Django_xm.apps.deep_research.views.ResearchTask.objects.get')
    def test_delete_view_success(self, mock_get_task):
        """测试成功删除任务"""
        mock_task = MagicMock()
        mock_task.task_id = self.test_task_id
        mock_get_task.return_value = mock_task

        url = reverse('deep_research:delete', kwargs={'task_id': self.test_task_id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_task.delete.assert_called_once()

    def test_delete_view_get_not_allowed(self):
        """测试删除视图不支持GET请求"""
        url = reverse('deep_research:delete', kwargs={'task_id': self.test_task_id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
