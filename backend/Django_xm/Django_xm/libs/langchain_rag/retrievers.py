"""
检索器模块

提供统一的检索器接口，支持多种检索策略：
- Similarity Search: 相似度检索
- MMR: 最大边际相关性检索
- Similarity Score Threshold: 相似度阈值过滤

同时提供将检索器封装为 LangChain Tool 的功能，用于 Agent。
"""

from typing import Optional, Literal
from langchain_core.vectorstores import VectorStore
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools.retriever import create_retriever_tool as lc_create_retriever_tool

from ..langchain_core.config import settings, get_logger

logger = get_logger(__name__)


SearchType = Literal["similarity", "mmr", "similarity_score_threshold"]


def create_retriever(
    vector_store: VectorStore,
    search_type: Optional[SearchType] = None,
    k: Optional[int] = None,
    score_threshold: Optional[float] = None,
    fetch_k: Optional[int] = None,
    **kwargs,
) -> BaseRetriever:
    """
    从向量库创建检索器

    Args:
        vector_store: 向量存储实例
        search_type: 检索类型
        k: 返回的文档数量
        score_threshold: 相似度阈值
        fetch_k: MMR 候选文档数量
    """
    search_type = search_type or settings.retriever_search_type
    k = k or settings.retriever_k
    score_threshold = score_threshold or settings.retriever_score_threshold
    fetch_k = fetch_k or settings.retriever_fetch_k

    logger.info(f"🔍 创建检索器: search_type={search_type}, k={k}")

    search_kwargs = {"k": k}

    if search_type == "mmr":
        search_kwargs["fetch_k"] = fetch_k

    elif search_type == "similarity_score_threshold":
        search_kwargs["score_threshold"] = score_threshold

    search_kwargs.update(kwargs)

    try:
        retriever = vector_store.as_retriever(
            search_type=search_type,
            search_kwargs=search_kwargs,
        )

        logger.info("✅ 检索器创建成功")
        return retriever

    except Exception as e:
        logger.error(f"❌ 创建检索器失败: {e}")
        raise


def create_retriever_tool(
    retriever: BaseRetriever,
    name: str = "knowledge_base",
    description: Optional[str] = None,
) -> any:
    """
    将检索器封装为 LangChain Tool
    """
    if description is None:
        description = (
            f"搜索 {name} 知识库中的相关信息。"
            "当需要回答关于文档内容的问题时使用此工具。"
            "输入应该是一个搜索查询。"
        )

    logger.info(f"🔧 创建检索器工具: {name}")

    try:
        tool = lc_create_retriever_tool(
            retriever=retriever,
            name=name,
            description=description,
        )

        logger.info("✅ 检索器工具创建成功")
        return tool

    except Exception as e:
        logger.error(f"❌ 创建检索器工具失败: {e}")
        raise


def test_retriever(
    retriever: BaseRetriever,
    query: str = "测试查询",
    show_results: bool = True,
) -> bool:
    """
    测试检索器是否正常工作
    """
    try:
        logger.info(f"🧪 测试检索器: query='{query}'")

        results = retriever.invoke(query)

        logger.info(f"✅ 检索器测试成功，找到 {len(results)} 个结果")

        if show_results:
            for i, doc in enumerate(results, 1):
                logger.info(f"   结果 {i}: {doc.page_content[:100]}...")

        return True

    except Exception as e:
        logger.error(f"❌ 检索器测试失败: {e}")
        return False


def create_multi_retriever(
    retrievers: list[tuple[BaseRetriever, float]],
    **kwargs,
) -> BaseRetriever:
    """
    创建多检索器（ensemble retriever）
    """
    try:
        from langchain.retrievers import EnsembleRetriever

        logger.info(f"🔗 创建组合检索器: {len(retrievers)} 个检索器")

        retriever_list = [r for r, _ in retrievers]
        weights = [w for _, w in retrievers]

        ensemble = EnsembleRetriever(
            retrievers=retriever_list,
            weights=weights,
            **kwargs,
        )

        logger.info("✅ 组合检索器创建成功")
        return ensemble

    except ImportError:
        logger.error("❌ EnsembleRetriever 不可用")
        raise
    except Exception as e:
        logger.error(f"❌ 创建组合检索器失败: {e}")
        raise


def get_retriever_config(search_type: str = "similarity") -> dict:
    """
    获取推荐的检索器配置
    """
    configs = {
        "similarity": {
            "search_type": "similarity",
            "k": 4,
            "description": "基本相似度检索，速度快",
        },
        "mmr": {
            "search_type": "mmr",
            "k": 4,
            "fetch_k": 20,
            "description": "最大边际相关性检索，结果更多样化",
        },
        "threshold": {
            "search_type": "similarity_score_threshold",
            "score_threshold": 0.7,
            "k": 10,
            "description": "相似度阈值过滤，只返回高质量结果",
        },
    }

    if search_type not in configs:
        logger.warning(f"未知的检索类型: {search_type}，使用默认配置")
        return configs["similarity"]

    config = configs[search_type].copy()
    config.pop("description", None)

    return config


def get_embeddings():
    """
    获取 Embeddings 实例
    """
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
    )
