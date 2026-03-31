"""
rag模块

提供 RAG（检索增强生成）相关功能：
- 文档加载与处理
- 向量存储与索引管理
- 检索器封装
- RAG Agent
"""

from .loaders import (
    load_document,
    load_directory,
    load_documents_from_directory,
    load_documents_from_paths,
    get_supported_extensions,
)
from .embeddings import (
    get_embeddings,
    get_embedding_dimension,
    estimate_embedding_cost,
    test_embeddings,
    get_embeddings_by_preset,
)
from .vector_stores import (
    create_vector_store,
    load_vector_store,
    save_vector_store,
    add_documents_to_vector_store,
    delete_vector_store,
    search_vector_store,
)
from .retrievers import (
    create_retriever,
    create_retriever_tool,
    test_retriever,
    create_multi_retriever,
    get_retriever_config,
)
from .splitters import (
    get_text_splitter,
    split_documents,
    split_text,
    get_optimal_chunk_size,
    analyze_chunks,
)
from .rag_agent import (
    create_rag_agent,
    query_rag_agent,
    format_rag_response,
    create_conversational_rag_agent,
)
from .index_manager import IndexManager

__all__ = [
    "load_document",
    "load_directory",
    "load_documents_from_directory",
    "load_documents_from_paths",
    "get_supported_extensions",
    "get_embeddings",
    "get_embedding_dimension",
    "estimate_embedding_cost",
    "test_embeddings",
    "get_embeddings_by_preset",
    "create_vector_store",
    "load_vector_store",
    "save_vector_store",
    "add_documents_to_vector_store",
    "delete_vector_store",
    "search_vector_store",
    "create_retriever",
    "create_retriever_tool",
    "test_retriever",
    "create_multi_retriever",
    "get_retriever_config",
    "get_text_splitter",
    "split_documents",
    "split_text",
    "get_optimal_chunk_size",
    "analyze_chunks",
    "create_rag_agent",
    "query_rag_agent",
    "format_rag_response",
    "create_conversational_rag_agent",
    "IndexManager",
]