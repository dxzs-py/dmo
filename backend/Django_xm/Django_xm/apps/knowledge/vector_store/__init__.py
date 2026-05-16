from typing import List, Optional
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore

from Django_xm.apps.config_center.config import get_logger

logger = get_logger(__name__)


def search_vector_store(
    vector_store: VectorStore,
    query: str,
    k: int = 4,
    score_threshold: Optional[float] = None,
) -> List[tuple]:
    logger.info(f"搜索向量库: query='{query[:50]}...', k={k}")

    try:
        results = vector_store.similarity_search_with_score(
            query=query,
            k=k,
        )

        if score_threshold is not None:
            results = [
                (doc, score) for doc, score in results
                if score >= score_threshold
            ]

        logger.info(f"找到 {len(results)} 个相关文档")
        return results

    except Exception as e:
        logger.error(f"搜索失败: {e}")
        raise
