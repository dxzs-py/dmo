"""
知识库视图模块

视图已按功能拆分到子模块中:
- views_kb.py: 知识库管理视图 (KnowledgeBase*)
- views_rag.py: RAG 索引/文档/查询视图 (RAG*, rag_query_stream)
- views_async.py: 异步 RAG 操作视图 (AsyncRAG*)
- views_utils.py: 公共工具函数

本文件重新导出所有视图类，保持向后兼容。
"""

from .views_kb import (
    KnowledgeBaseListView,
    KnowledgeBaseDetailView,
    KnowledgeBaseDocumentListView,
    KnowledgeBaseUploadView,
    KnowledgeBaseDocumentDeleteView,
    KnowledgeBaseSearchView,
)
from .views_rag import (
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
)
from .views_async import (
    AsyncRAGIndexCreateView,
    AsyncRAGDocumentUploadView,
    AsyncRAGIndexDeleteView,
)
from .views_utils import (
    get_file_extension,
    get_document_type,
    get_user_index_name,
    get_original_index_name,
)

__all__ = [
    'KnowledgeBaseListView',
    'KnowledgeBaseDetailView',
    'KnowledgeBaseDocumentListView',
    'KnowledgeBaseUploadView',
    'KnowledgeBaseDocumentDeleteView',
    'KnowledgeBaseSearchView',
    'RAGIndexCreateView',
    'RAGEmptyIndexCreateView',
    'RAGIndexListView',
    'RAGIndexDetailView',
    'RAGIndexDeleteView',
    'RAGIndexStatsView',
    'RAGDocumentUploadView',
    'RAGDocumentListView',
    'RAGDocumentDeleteView',
    'RAGDocumentAddDirectoryView',
    'RAGQueryView',
    'RAGSearchView',
    'rag_query_stream',
    'AsyncRAGIndexCreateView',
    'AsyncRAGDocumentUploadView',
    'AsyncRAGIndexDeleteView',
    'get_file_extension',
    'get_document_type',
    'get_user_index_name',
    'get_original_index_name',
]
