"""
Knowledge 服务层 - 提供知识库管理的所有服务接口

门面定位（A-1，2026-10-06 声明）：本包 __init__ 是 knowledge 服务层的
**便利入口 + 测试 monkeypatch 锚点**，不是生产消费的强制路径——生产代码
62+ 处均按子模块深层导入（chat/ai_engine/learning/attachments/context_manager/
research 与 knowledge 内部自身），仅个别测试经此包 import 用于 monkeypatch。
维持全量再导出不作收窄（B-051 重建有历史原因，删除留专项）；
新增公共 API 时同步在此登记，保证门面与深层路径能力一致。

包含：
- 知识库业务（CRUD、文档管理、搜索）
- 索引管理（创建、删除、加载、统计）
- 文档处理（加载、分块）
- 向量嵌入（Embedding）
- 检索器工厂（Retriever Factory）
- 关键词降级检索（Keyword Fallback，A-4 转正）
- Agent 检索工具（Retriever Tool）
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
from .keyword_fallback import (
    is_embedding_error,
    keyword_search_fallback,
    record_degradation_metric,
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
    "delete_document",
    "delete_knowledge_base",
    "enhance_chunk_metadata",
    "get_document_loader",
    "get_embedding_dimension",
    "get_embeddings",
    "get_knowledge_base_detail",
    "get_text_splitter",
    "is_embedding_error",
    "keyword_search_fallback",
    "list_documents",
    "list_knowledge_bases",
    "load_document",
    "load_documents_from_directory",
    "query_strict_rag",
    "record_degradation_metric",
    "search_knowledge_base",
    "split_documents",
    "stream_strict_rag",
    "update_knowledge_base",
    "upload_documents",
]
