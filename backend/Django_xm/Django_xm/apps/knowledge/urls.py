from django.urls import path

from .views import (
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
    path("indices/", KnowledgeIndexListView.as_view(), name="index_list"),
    path("indices/create/", KnowledgeBaseListView.as_view(), name="index_create"),
    path("indices/create-empty/", KnowledgeBaseListView.as_view(), name="index_create_empty"),
    path("indices/<str:kb_id>/", KnowledgeIndexDetailView.as_view(), name="index_detail"),
    path("indices/<str:kb_id>/delete/", KnowledgeBaseDetailView.as_view(), name="index_delete"),
    path("indices/<str:kb_id>/stats/", KnowledgeBaseDetailView.as_view(), name="index_stats"),
    path("indices/<str:kb_id>/upload/", KnowledgeBaseUploadView.as_view(), name="document_upload"),
    path("indices/<str:kb_id>/documents/", KnowledgeBaseDocumentListView.as_view(), name="document_list"),
    path(
        "indices/<str:kb_id>/documents/<str:filename>/",
        KnowledgeBaseDocumentDeleteView.as_view(),
        name="document_delete",
    ),
    # RAG 查询/检索/流式
    path("query/", RAGQueryView.as_view(), name="query"),
    path("search/", RAGSearchView.as_view(), name="search"),
    path("query/stream/", rag_query_stream, name="query_stream"),
    # 知识库管理
    # list/detail 端点使用 operationId 隔离子类，避免 drf_spectacular W001 冲突
    path("knowledge-bases/", KnowledgeBasesListView.as_view(), name="knowledge_bases_list"),
    path("knowledge-bases/<str:kb_id>/", KnowledgeBasesDetailView.as_view(), name="knowledge_bases_detail"),
    path(
        "knowledge-bases/<str:kb_id>/documents/",
        KnowledgeBaseDocumentListView.as_view(),
        name="knowledge_bases_documents_list",
    ),
    path("knowledge-bases/<str:kb_id>/upload/", KnowledgeBaseUploadView.as_view(), name="knowledge_bases_upload"),
    path(
        "knowledge-bases/<str:kb_id>/documents/<str:filename>/",
        KnowledgeBaseDocumentDeleteView.as_view(),
        name="knowledge_bases_document_delete",
    ),
    path("knowledge-bases/<str:kb_id>/search/", KnowledgeBaseSearchView.as_view(), name="knowledge_bases_search"),
]
