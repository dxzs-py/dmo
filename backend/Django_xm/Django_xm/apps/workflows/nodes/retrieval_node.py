"""
文档检索节点 (Retrieval Node)
"""

import logging
from typing import Dict, Any, List

from ..state import StudyFlowState, RetrievedDocument
from Django_xm.apps.rag.index_manager import IndexManager
from Django_xm.apps.rag.embeddings import get_embeddings
from Django_xm.apps.rag.retrievers import create_retriever
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


def retrieval_node(state: StudyFlowState) -> Dict[str, Any]:
    logger.info("[Retrieval Node] 开始检索相关文档")

    try:
        learning_plan = state.get("learning_plan")
        if not learning_plan:
            raise ValueError("学习计划不存在，无法进行文档检索")

        topic = learning_plan["topic"]
        key_points = learning_plan["key_points"]

        main_query = f"{topic}"
        logger.info(f"[Retrieval Node] 主查询: {main_query}")

        index_manager = IndexManager()
        embeddings = get_embeddings()

        try:
            vector_store = index_manager.load_index("test_index", embeddings)
            retriever = create_retriever(vector_store, k=5)

            logger.info("[Retrieval Node] 执行文档检索...")
            docs = retriever.invoke(main_query)
        except FileNotFoundError:
            logger.warning("[Retrieval Node] 索引不存在，返回空结果")
            docs = []

        retrieved_docs: List[RetrievedDocument] = []
        for i, doc in enumerate(docs):
            retrieved_doc: RetrievedDocument = {
                "content": doc.page_content,
                "metadata": doc.metadata,
                "relevance_score": 1.0 - (i * 0.1)
            }
            retrieved_docs.append(retrieved_doc)

        logger.info(f"[Retrieval Node] 检索到 {len(retrieved_docs)} 个相关文档")

        return {"retrieved_docs": retrieved_docs}

    except Exception as e:
        logger.error(f"[Retrieval Node] 检索文档失败: {e}")
        raise