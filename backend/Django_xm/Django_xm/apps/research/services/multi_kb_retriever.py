"""
多知识库联合检索服务

支持从多个知识库中联合检索，使用 EnsembleRetriever 合并结果。
为深度研究的文档分析节点提供 retriever_tool。
"""

from typing import List, Optional

from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import BaseTool

from Django_xm.apps.ai_engine.config import get_logger
from Django_xm.apps.knowledge.services.index_service import IndexManager
from Django_xm.apps.knowledge.services.retrieval_service import (
    create_retriever,
    create_retriever_tool,
    create_multi_retriever,
    get_embeddings,
)

logger = get_logger(__name__)


def build_multi_kb_retriever(
    knowledge_base_ids: List[str],
    user_id: int,
    k: int = 4,
    search_type: str = "mmr",
) -> Optional[BaseRetriever]:
    """
    根据知识库 ID 列表构建联合检索器

    Args:
        knowledge_base_ids: 知识库名称列表
        user_id: 用户 ID，用于构造 user 前缀索引名
        k: 每个检索器返回的文档数
        search_type: 检索策略

    Returns:
        EnsembleRetriever 或单个 BaseRetriever，失败返回 None
    """
    if not knowledge_base_ids:
        logger.warning("知识库 ID 列表为空，无法构建检索器")
        return None

    manager = IndexManager()
    embeddings = get_embeddings()

    retrievers = []
    loaded_names = []

    for kb_id in knowledge_base_ids:
        user_index_name = f"user_{user_id}_{kb_id}"
        try:
            if not manager.index_exists(user_index_name):
                logger.warning(f"知识库索引不存在: {user_index_name}")
                continue

            metadata = manager._load_metadata(user_index_name) or {}
            num_docs = metadata.get("num_documents", 0)
            if num_docs == 0:
                logger.warning(f"知识库为空，跳过: {user_index_name}")
                continue

            vector_store = manager.load_index(user_index_name, embeddings)
            retriever = create_retriever(
                vector_store,
                search_type=search_type,
                k=k,
            )
            retrievers.append(retriever)
            loaded_names.append(kb_id)
            logger.info(f"已加载知识库: {kb_id} (文档块: {num_docs})")
        except Exception as e:
            logger.error(f"加载知识库 {kb_id} 失败: {e}")
            continue

    if not retrievers:
        logger.warning("没有成功加载任何知识库")
        return None

    if len(retrievers) == 1:
        logger.info(f"仅一个知识库，使用单一检索器: {loaded_names[0]}")
        return retrievers[0]

    weights = [1.0 / len(retrievers)] * len(retrievers)
    ensemble = create_multi_retriever(retrievers, weights=weights)
    logger.info(f"已创建联合检索器，包含 {len(retrievers)} 个知识库: {loaded_names}")
    return ensemble


def build_retriever_tool_for_research(
    knowledge_base_ids: List[str],
    user_id: int,
    k: int = 4,
) -> Optional[BaseTool]:
    """
    为深度研究构建 retriever_tool

    Args:
        knowledge_base_ids: 知识库名称列表
        user_id: 用户 ID
        k: 每个检索器返回的文档数

    Returns:
        LangChain Tool 实例，失败返回 None
    """
    retriever = build_multi_kb_retriever(knowledge_base_ids, user_id, k=k)
    if retriever is None:
        return None

    if len(knowledge_base_ids) == 1:
        name = f"knowledge_base_{knowledge_base_ids[0]}"
        description = (
            f"搜索知识库「{knowledge_base_ids[0]}」中的相关文档。"
            "当需要回答关于该知识库文档内容的问题时使用此工具。"
            "输入应该是一个搜索查询。"
        )
    else:
        name = "knowledge_bases"
        kb_names = "、".join(knowledge_base_ids)
        description = (
            f"在多个知识库（{kb_names}）中联合搜索相关文档。"
            "当需要回答关于这些知识库文档内容的问题时使用此工具。"
            "输入应该是一个搜索查询。"
        )

    tool = create_retriever_tool(
        retriever=retriever,
        name=name,
        description=description,
    )
    logger.info(f"已创建研究用检索工具: {name}")
    return tool
