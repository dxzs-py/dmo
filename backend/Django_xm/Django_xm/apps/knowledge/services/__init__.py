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
    load_document,
    load_documents_from_directory,
)
from .embedding_service import (
    CachedEmbeddings,
    get_embedding_dimension,
    get_embeddings,
)
from .index_service import IndexManager
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
from .retrieval_service import (
    SearchType,
    create_multi_query_retriever,
    create_reranker,
    create_reranking_retriever,
    create_retriever,
    create_retriever_tool,
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
    "CachedEmbeddings",
    "GenerationMetrics",
    "IndexManager",
    "RAGEvaluationResult",
    "RAGEvaluator",
    "RetrievalMetrics",
    "SearchType",
    "SplitterType",
    "astream_strict_rag",
    "create_knowledge_base",
    "create_multi_query_retriever",
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
    "get_knowledge_base_detail",
    "get_text_splitter",
    "list_documents",
    "list_knowledge_bases",
    "load_document",
    "load_documents_from_directory",
    "query_strict_rag",
    "search_knowledge_base",
    "split_documents",
    "stream_strict_rag",
    "update_knowledge_base",
    "upload_documents",
]
