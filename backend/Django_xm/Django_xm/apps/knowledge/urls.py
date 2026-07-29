from django.urls import path

from .views import (
    AsyncRAGDocumentUploadView,
    AsyncRAGIndexCreateView,
    AsyncRAGIndexDeleteView,
    KnowledgeBaseDetailView,
    KnowledgeBaseDocumentDeleteView,
    KnowledgeBaseDocumentListView,
    KnowledgeBaseListView,
    KnowledgeBasesDetailView,
    KnowledgeBaseSearchView,
    KnowledgeBasesListView,
    KnowledgeBaseUploadView,
    KnowledgeIndexDetailView,
    KnowledgeIndexListView,
    RAGQueryView,
    RAGSearchView,
    rag_query_stream,
)

app_name = "knowledge"

urlpatterns = [
    # 索引管理 — 指向 views_kb 知识库视图
    # list/detail 端点使用 operationId 隔离子类，避免 drf_spectacular W001 冲突
    path("indices/", KnowledgeIndexListView.as_view(), name="index-list"),
    path("indices/create/", KnowledgeBaseListView.as_view(), name="index-create"),
    path("indices/create-empty/", KnowledgeBaseListView.as_view(), name="index-create-empty"),
    path("indices/<str:kb_id>/", KnowledgeIndexDetailView.as_view(), name="index-detail"),
    path("indices/<str:kb_id>/delete/", KnowledgeBaseDetailView.as_view(), name="index-delete"),
    path("indices/<str:kb_id>/stats/", KnowledgeBaseDetailView.as_view(), name="index-stats"),
    path("indices/<str:kb_id>/upload/", KnowledgeBaseUploadView.as_view(), name="document-upload"),
    path("indices/<str:kb_id>/documents/", KnowledgeBaseDocumentListView.as_view(), name="document-list"),
    path(
        "indices/<str:kb_id>/documents/<str:filename>/",
        KnowledgeBaseDocumentDeleteView.as_view(),
        name="document-delete",
    ),
    path("indices/<str:kb_id>/add-directory/", KnowledgeBaseUploadView.as_view(), name="document-add-directory"),
    # RAG 查询/检索/流式
    path("query/", RAGQueryView.as_view(), name="query"),
    path("search/", RAGSearchView.as_view(), name="search"),
    path("query/stream/", rag_query_stream, name="query-stream"),
    # 知识库管理
    # list/detail 端点使用 operationId 隔离子类，避免 drf_spectacular W001 冲突
    path("knowledge-bases/", KnowledgeBasesListView.as_view(), name="knowledge-bases-list"),
    path("knowledge-bases/<str:kb_id>/", KnowledgeBasesDetailView.as_view(), name="knowledge-bases-detail"),
    path(
        "knowledge-bases/<str:kb_id>/documents/",
        KnowledgeBaseDocumentListView.as_view(),
        name="knowledge-bases-documents-list",
    ),
    path("knowledge-bases/<str:kb_id>/upload/", KnowledgeBaseUploadView.as_view(), name="knowledge-bases-upload"),
    path(
        "knowledge-bases/<str:kb_id>/documents/<str:filename>/",
        KnowledgeBaseDocumentDeleteView.as_view(),
        name="knowledge-bases-document-delete",
    ),
    path("knowledge-bases/<str:kb_id>/search/", KnowledgeBaseSearchView.as_view(), name="knowledge-bases-search"),
    # 异步 RAG 操作
    path("async/indices/", AsyncRAGIndexCreateView.as_view(), name="async-index-create"),
    path("async/indices/<str:kb_id>/upload/", AsyncRAGDocumentUploadView.as_view(), name="async-document-upload"),
    path("async/indices/<str:kb_id>/delete/", AsyncRAGIndexDeleteView.as_view(), name="async-index-delete"),
]
