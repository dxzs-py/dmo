"""
RAG视图测试
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class RAGViewTests(TestCase):
    """RAG视图测试"""

    def setUp(self):
        """测试前准备"""
        self.client = APIClient()
        self.test_index_name = 'test_index'

    def test_index_create_view_get_not_allowed(self):
        """测试索引创建视图不支持GET请求"""
        url = reverse('rag:index-create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.rag.index_manager.IndexManager')
    def test_index_create_view_success(self, mock_manager):
        """测试成功创建索引"""
        mock_instance = mock_manager.return_value
        mock_instance.create_index.return_value = {'name': self.test_index_name, 'status': 'created'}

        url = reverse('rag:index-create')
        data = {'name': self.test_index_name}
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('name', response.data)
        mock_instance.create_index.assert_called_once()

    def test_index_create_view_missing_name(self):
        """测试创建索引缺少name参数"""
        url = reverse('rag:index-create')
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('name', response.data)

    def test_index_list_view(self):
        """测试获取索引列表"""
        url = reverse('rag:index-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('indexes', response.data)

    @patch('Django_xm.apps.rag.index_manager.IndexManager')
    def test_index_detail_view_success(self, mock_manager):
        """测试获取索引详情"""
        mock_instance = mock_manager.return_value
        mock_instance.get_index_stats.return_value = {
            'name': self.test_index_name,
            'document_count': 10,
            'chunk_count': 100
        }

        url = reverse('rag:index-detail', kwargs={'name': self.test_index_name})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('name', response.data)

    @patch('Django_xm.apps.rag.index_manager.IndexManager')
    def test_index_delete_view_success(self, mock_manager):
        """测试删除索引"""
        mock_instance = mock_manager.return_value
        mock_instance.delete_index.return_value = True

        url = reverse('rag:index-delete', kwargs={'name': self.test_index_name})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_instance.delete_index.assert_called_once_with(self.test_index_name)

    def test_query_view_get_not_allowed(self):
        """测试查询视图不支持GET请求"""
        url = reverse('rag:query')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch('Django_xm.apps.rag.create_rag_agent')
    def test_query_view_success(self, mock_create_agent):
        """测试成功查询RAG"""
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {'answer': '这是答案', 'sources': ['doc1']}
        mock_create_agent.return_value = mock_agent

        url = reverse('rag:query')
        data = {
            'query': '什么是LangChain？',
            'index_name': self.test_index_name
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('answer', response.data)

    def test_query_view_missing_query(self):
        """测试查询缺少query参数"""
        url = reverse('rag:query')
        data = {'index_name': self.test_index_name}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('query', response.data)

    @patch('Django_xm.apps.rag.create_rag_agent')
    def test_search_view_success(self, mock_create_agent):
        """测试纯检索查询"""
        mock_agent = MagicMock()
        mock_agent.as_retriever.return_value.get_relevant_documents.return_value = [
            MagicMock(page_content='文档1内容', metadata={'source': 'doc1'}),
            MagicMock(page_content='文档2内容', metadata={'source': 'doc2'})
        ]
        mock_create_agent.return_value = mock_agent

        url = reverse('rag:search')
        data = {
            'query': '搜索测试',
            'index_name': self.test_index_name,
            'k': 2
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('documents', response.data)

    @patch('Django_xm.apps.rag.index_manager.IndexManager')
    def test_upload_documents_view_success(self, mock_manager):
        """测试上传文档"""
        mock_instance = mock_manager.return_value
        mock_instance.add_documents.return_value = {'added': 5, 'total': 5}

        url = reverse('rag:upload')
        data = {
            'index_name': self.test_index_name,
            'documents': [
                {'content': '文档1内容', 'metadata': {'source': 'doc1'}},
                {'content': '文档2内容', 'metadata': {'source': 'doc2'}}
            ]
        }
        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('added', response.data)

    def test_upload_documents_view_missing_documents(self):
        """测试上传文档缺少documents参数"""
        url = reverse('rag:upload')
        data = {'index_name': self.test_index_name}
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('documents', response.data)
