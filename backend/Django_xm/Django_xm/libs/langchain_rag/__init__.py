"""
langchain_rag 模块

提供 RAG（检索增强生成）相关功能，包括：
- 文档加载与处理
- 向量存储与索引管理
- 检索器封装
- RAG Agent
"""

from .index_manager import (
    IndexManager,
    create_vector_store,
    save_vector_store,
    load_vector_store,
    add_documents_to_vector_store,
    search_vector_store,
    delete_vector_store,
    get_vector_store_stats,
)
from .retrievers import (
    create_retriever,
    create_retriever_tool,
    test_retriever,
    create_multi_retriever,
    get_retriever_config,
    get_embeddings,
    SearchType,
)
from .loaders import (
    get_document_loader,
    load_document,
    load_documents_from_directory,
)

__all__ = [
    "IndexManager",
    "create_vector_store",
    "save_vector_store",
    "load_vector_store",
    "add_documents_to_vector_store",
    "search_vector_store",
    "delete_vector_store",
    "get_vector_store_stats",
    "create_retriever",
    "create_retriever_tool",
    "test_retriever",
    "create_multi_retriever",
    "get_retriever_config",
    "get_embeddings",
    "SearchType",
    "get_document_loader",
    "load_document",
    "load_documents_from_directory",
]
