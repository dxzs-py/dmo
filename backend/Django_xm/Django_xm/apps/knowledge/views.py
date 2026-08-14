"""
知识库视图模块

视图已按功能拆分到子模块中:
- views_kb.py: 知识库管理视图 (KnowledgeBase*) — 主要入口（含异步上传任务提交）
- views_rag.py: RAG 查询/检索/流式视图 (RAGQueryView, RAGSearchView, rag_query_stream)
- views_utils.py: 公共工具函数

本文件重新导出所有视图类，保持向后兼容。
"""

from .views_kb import (
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
)
from .views_rag import (
    RAGQueryView,
    RAGSearchView,
    rag_query_stream,
)
from .views_utils import (
    get_document_type,
    get_file_extension,
    get_original_index_name,
    get_user_index_name,
)

__all__ = [
    "KnowledgeBaseDetailView",
    "KnowledgeBaseDocumentDeleteView",
    "KnowledgeBaseDocumentListView",
    "KnowledgeBaseListView",
    "KnowledgeBaseSearchView",
    "KnowledgeBaseUploadView",
    "KnowledgeBasesDetailView",
    "KnowledgeBasesListView",
    "KnowledgeIndexDetailView",
    "KnowledgeIndexListView",
    "RAGQueryView",
    "RAGSearchView",
    "get_document_type",
    "get_file_extension",
    "get_original_index_name",
    "get_user_index_name",
    "rag_query_stream",
]
