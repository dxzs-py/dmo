from django.urls import path
from .views import (
    RAGIndexCreateView,
    RAGIndexListView,
    RAGIndexDetailView,
    RAGIndexDeleteView,
    RAGIndexStatsView,
    RAGDocumentUploadView,
    RAGDocumentAddDirectoryView,
    RAGQueryView,
    RAGSearchView,
    rag_query_stream,
)

app_name = 'rag'

urlpatterns = [
    # 索引管理
    path('index/', RAGIndexCreateView.as_view(), name='index-create'),
    path('index/list/', RAGIndexListView.as_view(), name='index-list'),
    path('index/<str:name>/', RAGIndexDetailView.as_view(), name='index-detail'),
    path('index/<str:name>/delete/', RAGIndexDeleteView.as_view(), name='index-delete'),
    path('index/<str:name>/stats/', RAGIndexStatsView.as_view(), name='index-stats'),
    
    # 文档管理
    path('index/<str:name>/upload/', RAGDocumentUploadView.as_view(), name='document-upload'),
    path('index/<str:name>/add-directory/', RAGDocumentAddDirectoryView.as_view(), name='document-add-directory'),
    
    # 查询接口
    path('query/', RAGQueryView.as_view(), name='query'),
    path('search/', RAGSearchView.as_view(), name='search'),
    path('query/stream/', rag_query_stream, name='query-stream'),
]
