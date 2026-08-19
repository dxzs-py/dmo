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

from .document_service import (
    get_document_loader,
    get_supported_extensions,
    load_document,
    load_documents_from_directory,
)
from .embedding_service import (
    EMBEDDING_CONFIGS,
    CachedEmbeddings,
    get_embedding_dimension,
    get_embeddings,
    get_embeddings_by_preset,
    test_embeddings,
)
from .index_service import (
    IndexManager,
)
from .kb_service import (
    create_knowledge_base,
    delete_document,
    delete_knowledge_base,
    get_knowledge_base_detail,
    list_documents,
    list_knowledge_bases,
    search_knowledge_base,
    update_knowledge_base,
    upload_documents,
)
from .rag_evaluation import (
    GenerationMetrics,
    RAGEvaluationResult,
    RAGEvaluator,
    RetrievalMetrics,
)

# FallbackEmbeddings / EMBEDDING_FALLBACK_REGISTRY 已迁移到 ai_engine.services.embedding_factory
# 请直接使用: from Django_xm.apps.ai_engine.services.embedding_factory import FallbackEmbedding
from .retrieval_service import (
    SearchType,
    create_advanced_retriever,
    create_multi_query_retriever,
    create_multi_retriever,
    create_reranker,
    create_reranking_retriever,
    create_retriever,
    create_retriever_tool,
    get_retriever_config,
    test_retriever,
)
from .splitters import (
    SplitterType,
    enhance_chunk_metadata,
    get_text_splitter,
    split_documents,
)
from .strict_rag_chain import (
    astream_strict_rag,
    query_strict_rag,
    stream_strict_rag,
)

__all__ = [
    "EMBEDDING_CONFIGS",
    "CachedEmbeddings",
    "GenerationMetrics",
    "IndexManager",
    "RAGEvaluationResult",
    "RAGEvaluator",
    "RetrievalMetrics",
    "SearchType",
    "SplitterType",
    "astream_strict_rag",
    "create_advanced_retriever",
    "create_knowledge_base",
    "create_multi_query_retriever",
    "create_multi_retriever",
    "create_reranker",
    "create_reranking_retriever",
    "create_retriever",
    "create_retriever_tool",
    "delete_document",
    "delete_knowledge_base",
    "enhance_chunk_metadata",
    "get_document_loader",
    "get_embedding_dimension",
    "get_embeddings",
    "get_embeddings_by_preset",
    "get_knowledge_base_detail",
    "get_retriever_config",
    "get_supported_extensions",
    "get_text_splitter",
    "list_documents",
    "list_knowledge_bases",
    "load_document",
    "load_documents_from_directory",
    "query_strict_rag",
    "search_knowledge_base",
    "split_documents",
    "stream_strict_rag",
    "test_embeddings",
    "test_retriever",
    "update_knowledge_base",
    "upload_documents",
]
