from django.urls import path
from .views import (
    RAGIndexCreateView,
    RAGEmptyIndexCreateView,
    RAGIndexListView,
    RAGIndexDetailView,
    RAGIndexDeleteView,
    RAGIndexStatsView,
    RAGDocumentUploadView,
    RAGDocumentListView,
    RAGDocumentDeleteView,
    RAGDocumentAddDirectoryView,
    RAGQueryView,
    RAGSearchView,
    rag_query_stream,
    KnowledgeBaseListView,
    KnowledgeBaseDetailView,
    KnowledgeBaseUploadView,
    KnowledgeBaseDocumentListView,
    KnowledgeBaseDocumentDeleteView,
    KnowledgeBaseSearchView,
    AsyncRAGIndexCreateView,
    AsyncRAGDocumentUploadView,
    AsyncRAGIndexDeleteView,
)
from .cache_views import (
    CacheHealthCheckView,
    CacheStatsView,
    CacheClearView,
)

app_name = 'rag'

urlpatterns = [
    # 索引管理
    path('indices/', RAGIndexListView.as_view(), name='index-list'),
    path('indices/create/', RAGIndexCreateView.as_view(), name='index-create'),
    path('indices/create-empty/', RAGEmptyIndexCreateView.as_view(), name='index-create-empty'),
    path('indices/<str:name>/', RAGIndexDetailView.as_view(), name='index-detail'),
    path('indices/<str:name>/delete/', RAGIndexDeleteView.as_view(), name='index-delete'),
    path('indices/<str:name>/stats/', RAGIndexStatsView.as_view(), name='index-stats'),

    # 文档管理
    path('indices/<str:name>/upload/', RAGDocumentUploadView.as_view(), name='document-upload'),
    path('indices/<str:name>/documents/', RAGDocumentListView.as_view(), name='document-list'),
    path('indices/<str:name>/documents/<str:filename>/', RAGDocumentDeleteView.as_view(), name='document-delete'),
    path('indices/<str:name>/add-directory/', RAGDocumentAddDirectoryView.as_view(), name='document-add-directory'),

    # 查询接口
    path('query/', RAGQueryView.as_view(), name='query'),
    path('search/', RAGSearchView.as_view(), name='search'),
    path('query/stream/', rag_query_stream, name='query-stream'),

    # 知识库管理接口
    path('knowledge-bases/', KnowledgeBaseListView.as_view(), name='knowledge-bases-list'),
    path('knowledge-bases/<str:kb_id>/', KnowledgeBaseDetailView.as_view(), name='knowledge-bases-detail'),
    path('knowledge-bases/<str:kb_id>/documents/', KnowledgeBaseDocumentListView.as_view(), name='knowledge-bases-documents-list'),
    path('knowledge-bases/<str:kb_id>/upload/', KnowledgeBaseUploadView.as_view(), name='knowledge-bases-upload'),
    path('knowledge-bases/<str:kb_id>/documents/<str:filename>/', KnowledgeBaseDocumentDeleteView.as_view(), name='knowledge-bases-document-delete'),
    path('knowledge-bases/<str:kb_id>/search/', KnowledgeBaseSearchView.as_view(), name='knowledge-bases-search'),

    # 异步操作接口
    path('async/indices/', AsyncRAGIndexCreateView.as_view(), name='async-index-create'),
    path('async/indices/<str:name>/upload/', AsyncRAGDocumentUploadView.as_view(), name='async-document-upload'),
    path('async/indices/<str:name>/delete/', AsyncRAGIndexDeleteView.as_view(), name='async-index-delete'),

    # 缓存管理接口
    path('cache/health/', CacheHealthCheckView.as_view(), name='cache-health'),
    path('cache/stats/', CacheStatsView.as_view(), name='cache-stats'),
    path('cache/clear/', CacheClearView.as_view(), name='cache-clear'),
]
