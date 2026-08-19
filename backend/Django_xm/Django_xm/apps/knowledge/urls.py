from django.urls import path

from .views import (
    KnowledgeBaseDetailView,
    KnowledgeBaseDocumentDeleteView,
    KnowledgeBaseDocumentListView,
    KnowledgeBaseListView,
    KnowledgeBaseSearchView,
    RAGQueryView,
    RAGSearchView,
    rag_query_stream,
)

app_name = "knowledge"

urlpatterns = [
    # RAG 查询/检索/流式
    path("query/", RAGQueryView.as_view(), name="query"),
    path("search/", RAGSearchView.as_view(), name="search"),
    path("query/stream/", rag_query_stream, name="query_stream"),
    # 知识库管理（资源化路由，dj-07 收敛：indices/ 旧路由已删除）
    path("knowledge-bases/", KnowledgeBaseListView.as_view(), name="knowledge_bases_list"),
    path("knowledge-bases/<str:kb_id>/", KnowledgeBaseDetailView.as_view(), name="knowledge_bases_detail"),
    path(
        "knowledge-bases/<str:kb_id>/documents/",
        KnowledgeBaseDocumentListView.as_view(),
        name="knowledge_bases_documents_list",
    ),
    path(
        "knowledge-bases/<str:kb_id>/documents/<str:filename>/",
        KnowledgeBaseDocumentDeleteView.as_view(),
        name="knowledge_bases_document_delete",
    ),
    path("knowledge-bases/<str:kb_id>/search/", KnowledgeBaseSearchView.as_view(), name="knowledge_bases_search"),
]
