"""
rag模块

提供 RAG（检索增强生成）相关功能：
- 文档加载与处理
- 向量存储与索引管理
- 检索器封装
- RAG Agent
"""

from .loaders import load_document, load_documents_from_directory, get_supported_extensions
from .embeddings import get_embeddings
from .vector_stores import (
    create_vector_store,
    load_vector_store,
    save_vector_store,
    add_documents_to_vector_store,
    delete_vector_store,
    search_vector_store,
)
from .retrievers import create_retriever, create_retriever_tool
from .rag_agent import create_rag_agent, query_rag_agent
from .index_manager import IndexManager

__all__ = [
    "load_document",
    "load_documents_from_directory",
    "get_supported_extensions",
    "get_embeddings",
    "create_vector_store",
    "load_vector_store",
    "save_vector_store",
    "add_documents_to_vector_store",
    "delete_vector_store",
    "search_vector_store",
    "create_retriever",
    "create_retriever_tool",
    "create_rag_agent",
    "query_rag_agent",
    "IndexManager",
]