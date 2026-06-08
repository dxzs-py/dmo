"""
Knowledge 服务层 - 提供知识库管理的所有服务接口

包含：
- 知识库业务（CRUD、文档管理、搜索）
- 索引管理（创建、删除、加载、统计）
- 文档处理（加载、分块）
- 向量嵌入（Embedding）
- 检索服务（Retriever）
- RAG Chain（严格模式，防幻觉）
- RAG 评估（Evaluation）
"""

from .kb_service import (
    list_knowledge_bases,
    create_knowledge_base,
    get_knowledge_base_detail,
    update_knowledge_base,
    delete_knowledge_base,
    list_documents,
    upload_documents,
    delete_document,
    search_knowledge_base,
)
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
    enhance_chunk_metadata,
    SplitterType,
)
from .embedding_service import (
    get_embeddings,
    get_embedding_dimension,
    get_embeddings_by_preset,
    test_embeddings,
    EMBEDDING_CONFIGS,
    CachedEmbeddings,
)
# FallbackEmbeddings / EMBEDDING_FALLBACK_REGISTRY 已迁移到 ai_engine.services.embedding_factory
# 请直接使用: from Django_xm.apps.ai_engine.services.embedding_factory import FallbackEmbedding
from .retrieval_service import (
    create_retriever,
    create_retriever_tool,
    create_multi_retriever,
    create_reranker,
    create_reranking_retriever,
    create_multi_query_retriever,
    create_advanced_retriever,
    get_retriever_config,
    test_retriever,
    SearchType,
)
from .strict_rag_chain import (
    create_strict_rag_chain,
    query_strict_rag,
    aquery_strict_rag,
    astream_strict_rag,
    stream_strict_rag,
    STRICT_RAG_SYSTEM_PROMPT,
    STRICT_RAG_QA_PROMPT,
)
from .rag_evaluation import (
    RAGEvaluator,
    RAGEvaluationResult,
    RetrievalMetrics,
    GenerationMetrics,
)

__all__ = [
    "list_knowledge_bases",
    "create_knowledge_base",
    "get_knowledge_base_detail",
    "update_knowledge_base",
    "delete_knowledge_base",
    "list_documents",
    "upload_documents",
    "delete_document",
    "search_knowledge_base",
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
    "enhance_chunk_metadata",
    "SplitterType",
    "get_embeddings",
    "get_embedding_dimension",
    "get_embeddings_by_preset",
    "test_embeddings",
    "EMBEDDING_CONFIGS",
    "CachedEmbeddings",
    "create_retriever",
    "create_retriever_tool",
    "create_multi_retriever",
    "create_reranker",
    "create_reranking_retriever",
    "create_multi_query_retriever",
    "create_advanced_retriever",
    "get_retriever_config",
    "test_retriever",
    "SearchType",
    "create_strict_rag_chain",
    "query_strict_rag",
    "aquery_strict_rag",
    "astream_strict_rag",
    "stream_strict_rag",
    "STRICT_RAG_SYSTEM_PROMPT",
    "STRICT_RAG_QA_PROMPT",
    "RAGEvaluator",
    "RAGEvaluationResult",
    "RetrievalMetrics",
    "GenerationMetrics",
]
