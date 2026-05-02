"""
Knowledge 服务层 - 提供知识库管理的所有服务接口

包含：
- 索引管理（创建、删除、加载、统计）
- 文档处理（加载、分块）
- 向量嵌入（Embedding）
- 检索服务（Retriever）
- RAG Agent（基础 & 安全版）
"""

from .index_service import (
    IndexManager,
    create_vector_store,
    save_vector_store,
    load_vector_store,
)
from .document_service import (
    load_document,
    load_documents_from_directory,
    get_supported_extensions,
    get_document_loader,
)
from .splitters import (
    split_documents,
    get_text_splitter,
    SplitterType,
)
from .embedding_service import (
    get_embeddings,
    get_embedding_dimension,
    get_embeddings_by_preset,
    estimate_embedding_cost,
    test_embeddings,
    EMBEDDING_CONFIGS,
)
from .retrieval_service import (
    create_retriever,
    create_retriever_tool,
    create_multi_retriever,
    get_retriever_config,
    test_retriever,
    SearchType,
)
from .rag_agent import (
    create_rag_agent,
    create_conversational_rag_agent,
    query_rag_agent,
    aquery_rag_agent,
    format_rag_response,
    DEFAULT_RAG_SYSTEM_PROMPT,
)
from .safe_rag_agent import (
    create_safe_rag_agent,
    SafeRAGAgent,
    SAFE_RAG_SYSTEM_PROMPT,
)

__all__ = [
    "IndexManager",
    "create_vector_store",
    "save_vector_store",
    "load_vector_store",
    "load_document",
    "load_documents_from_directory",
    "get_supported_extensions",
    "get_document_loader",
    "split_documents",
    "get_text_splitter",
    "SplitterType",
    "get_embeddings",
    "get_embedding_dimension",
    "get_embeddings_by_preset",
    "estimate_embedding_cost",
    "test_embeddings",
    "EMBEDDING_CONFIGS",
    "create_retriever",
    "create_retriever_tool",
    "create_multi_retriever",
    "get_retriever_config",
    "test_retriever",
    "SearchType",
    "create_rag_agent",
    "create_conversational_rag_agent",
    "query_rag_agent",
    "aquery_rag_agent",
    "format_rag_response",
    "DEFAULT_RAG_SYSTEM_PROMPT",
    "create_safe_rag_agent",
    "SafeRAGAgent",
    "SAFE_RAG_SYSTEM_PROMPT",
]
