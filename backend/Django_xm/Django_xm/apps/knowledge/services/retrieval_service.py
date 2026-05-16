"""
检索器模块
提供统一的检索器接口，支持多种检索策略
"""

from typing import Optional, Literal, List

from langchain_core.vectorstores import VectorStore
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import BaseTool
from langchain_core.tools.retriever import create_retriever_tool as lc_create_retriever_tool
from langchain_core.documents import Document

from Django_xm.apps.ai_engine.config import settings, get_logger

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
    search_type = search_type or settings.retriever_search_type
    k = k or settings.retriever_k
    score_threshold = score_threshold or settings.retriever_score_threshold
    fetch_k = fetch_k or settings.retriever_fetch_k

    logger.info(f"创建检索器: search_type={search_type}, k={k}")

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
        logger.info("检索器创建成功")
        return retriever
    except Exception as e:
        logger.error(f"创建检索器失败: {e}")
        raise


def create_parent_document_retriever(
    vector_store: VectorStore,
    docstore=None,
    child_splitter=None,
    parent_splitter=None,
    child_k: int = 20,
    **kwargs,
) -> BaseRetriever:
    """
    创建 ParentDocumentRetriever

    核心思路：小块嵌入检索，返回包含该小块的完整父文档。
    解决长文档分块后上下文碎片化问题。

    Args:
        vector_store: 用于存储子文档嵌入的向量库
        docstore: 存储父文档的 docstore（默认 InMemoryDocstore）
        child_splitter: 子文档分块器（小块，用于检索）
        parent_splitter: 父文档分块器（大块，用于返回），None 表示不分块
        child_k: 检索子文档数量

    Returns:
        ParentDocumentRetriever 实例
    """
    try:
        from langchain.retrievers import ParentDocumentRetriever
    except ImportError:
        logger.error("ParentDocumentRetriever 不可用，请升级 langchain")
        raise

    if docstore is None:
        from langchain.storage import InMemoryStore
        docstore = InMemoryStore()

    if child_splitter is None:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        child_splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)

    retriever = ParentDocumentRetriever(
        vectorstore=vector_store,
        docstore=docstore,
        child_splitter=child_splitter,
        parent_splitter=parent_splitter,
        search_kwargs={"k": child_k},
        **kwargs,
    )

    logger.info(f"ParentDocumentRetriever 创建成功 (child_k={child_k})")
    return retriever


def create_retriever_tool(
    retriever: BaseRetriever,
    name: str = "knowledge_base",
    description: Optional[str] = None,
) -> BaseTool:
    """将检索器封装为 LangChain Tool"""
    if description is None:
        description = (
            f"搜索 {name} 知识库中的相关信息。"
            "当需要回答关于文档内容的问题时使用此工具。"
            "输入应该是一个搜索查询。"
        )

    logger.info(f"创建检索器工具: {name}")

    try:
        tool = lc_create_retriever_tool(
            retriever=retriever,
            name=name,
            description=description,
        )
        logger.info("检索器工具创建成功")
        return tool
    except Exception as e:
        logger.error(f"创建检索器工具失败: {e}")
        raise


def test_retriever(
    retriever: BaseRetriever,
    query: str = "测试查询",
    show_results: bool = True,
) -> bool:
    """测试检索器是否正常工作"""
    try:
        logger.info(f"🧪 测试检索器: query='{query}'")

        docs = retriever.invoke(query)

        logger.info(f"✅ 检索成功: 找到 {len(docs)} 个文档")

        if show_results and docs:
            logger.info("📄 检索结果:")
            for i, doc in enumerate(docs, 1):
                logger.info(f"   [{i}] {doc.page_content[:100]}...")
                if doc.metadata:
                    logger.info(f"       元数据: {doc.metadata}")

        return True

    except Exception as e:
        logger.error(f"❌ 检索器测试失败: {e}")
        return False


def create_multi_retriever(
    retrievers: list,
    weights: Optional[list] = None,
    **kwargs,
) -> BaseRetriever:
    """创建多检索器（ensemble retriever）"""
    try:
        from langchain_community.retrievers import EnsembleRetriever

        logger.info(f"🔗 创建组合检索器: {len(retrievers)} 个检索器")

        if weights is None:
            weights = [1.0 / len(retrievers)] * len(retrievers)

        ensemble = EnsembleRetriever(
            retrievers=retrievers,
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
    """获取推荐的检索器配置"""
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
    logger.info(f"📋 推荐的检索器配置 ({search_type}):")
    logger.info(f"   {config.get('description', '')}")

    config.pop("description", None)

    return config