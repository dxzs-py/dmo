"""
向量存储模块

提供统一的向量数据库接口，支持多种向量存储后端：
- FAISS: 本地向量库，支持持久化（推荐）
- InMemoryVectorStore: 内存向量库，用于开发测试
"""

import os
from pathlib import Path
from typing import List, Optional, Literal
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore, InMemoryVectorStore

try:
    from langchain_community.vectorstores import FAISS
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

import logging

logger = logging.getLogger(__name__)


VectorStoreType = Literal["faiss", "inmemory"]


def get_vector_store_path() -> str:
    try:
        from Django_xm.apps.core.config import settings
        return getattr(settings, 'vector_store_path', "./data/indexes")
    except ImportError:
        import os
        return os.environ.get("VECTOR_STORE_PATH", "./data/indexes")


def create_vector_store(
    documents: List[Document],
    embeddings: Embeddings,
    store_type: Optional[VectorStoreType] = None,
    **kwargs,
) -> VectorStore:
    if not documents:
        raise ValueError("文档列表不能为空")

    try:
        from Django_xm.apps.core.config import settings
        store_type = store_type or getattr(settings, 'vector_store_type', 'faiss')
    except ImportError:
        store_type = store_type or "faiss"

    logger.info(f"🗄️  创建向量存储: type={store_type}, documents={len(documents)}")

    try:
        if store_type == "faiss":
            if not FAISS_AVAILABLE:
                raise ImportError("FAISS 未安装，请安装: pip install faiss-cpu")
            vector_store = FAISS.from_documents(documents, embeddings, **kwargs)
            logger.info("✅ FAISS 向量库创建成功")
            return vector_store

        elif store_type == "inmemory":
            vector_store = InMemoryVectorStore(embeddings=embeddings)
            vector_store.add_documents(documents)
            logger.info("✅ 内存向量库创建成功")
            return vector_store

        else:
            raise ValueError(f"不支持的向量库类型: {store_type}")

    except Exception as e:
        logger.error(f"❌ 创建向量库失败: {e}")
        raise


def load_vector_store(
    index_path: str,
    embeddings: Embeddings,
    store_type: Optional[VectorStoreType] = None,
) -> VectorStore:
    index_dir = Path(index_path)

    if not index_dir.exists():
        raise FileNotFoundError(f"索引目录不存在: {index_path}")

    try:
        from Django_xm.apps.core.config import settings
        store_type = store_type or getattr(settings, 'vector_store_type', 'faiss')
    except ImportError:
        store_type = store_type or "faiss"

    logger.info(f"📂 加载向量存储: {index_path}, type={store_type}")

    try:
        if store_type == "faiss":
            if not FAISS_AVAILABLE:
                raise ImportError("FAISS 未安装")
            vector_store = FAISS.load_local(
                index_path,
                embeddings,
                allow_dangerous_deserialization=True
            )
            logger.info("✅ FAISS 向量库加载成功")
            return vector_store

        elif store_type == "inmemory":
            pkl_path = index_dir / "index.pkl"
            if not pkl_path.exists():
                raise FileNotFoundError(f"索引文件不存在: {pkl_path}")
            import pickle
            with open(pkl_path, "rb") as f:
                vector_store = pickle.load(f)
            return vector_store

        else:
            raise ValueError(f"不支持的向量库类型: {store_type}")

    except Exception as e:
        logger.error(f"❌ 加载向量库失败: {e}")
        raise


def save_vector_store(vector_store: VectorStore, index_path: str) -> None:
    index_dir = Path(index_path)
    index_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"💾 保存向量存储到: {index_path}")

    try:
        if hasattr(vector_store, "save_local"):
            vector_store.save_local(index_path)
            logger.info("✅ 向量库保存成功")
        else:
            import pickle
            pkl_path = index_dir / "index.pkl"
            with open(pkl_path, "wb") as f:
                pickle.dump(vector_store, f)
            logger.info("✅ 向量库保存成功（pickle格式）")

    except Exception as e:
        logger.error(f"❌ 保存向量库失败: {e}")
        raise


def add_documents_to_vector_store(
    vector_store: VectorStore,
    documents: List[Document],
) -> None:
    logger.info(f"➕ 添加 {len(documents)} 个文档到向量库")

    try:
        vector_store.add_documents(documents)
        logger.info("✅ 文档添加成功")
    except Exception as e:
        logger.error(f"❌ 添加文档失败: {e}")
        raise


def delete_vector_store(index_path: str) -> None:
    import shutil

    index_dir = Path(index_path)

    if not index_dir.exists():
        logger.warning(f"⚠️ 索引目录不存在: {index_path}")
        return

    try:
        shutil.rmtree(index_dir)
        logger.info(f"🗑️ 删除向量库: {index_path}")
    except Exception as e:
        logger.error(f"❌ 删除向量库失败: {e}")
        raise


def get_vector_store_stats(vector_store: VectorStore) -> dict:
    try:
        doc_count = vector_store.docstore._docstore.__len__()
    except Exception:
        doc_count = 0

    return {
        "type": type(vector_store).__name__,
        "document_count": doc_count,
    }


def search_vector_store(
    vector_store: VectorStore,
    query: str,
    k: int = 4,
    score_threshold: Optional[float] = None,
) -> List[tuple[Document, float]]:
    """
    在向量库中搜索相似文档

    Args:
        vector_store: 向量存储实例
        query: 查询文本
        k: 返回的文档数量
        score_threshold: 相似度阈值（可选）

    Returns:
        (Document, score) 元组列表，按相似度降序排列
    """
    logger.info(f"🔍 搜索向量库: query='{query[:50]}...', k={k}")

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

        logger.info(f"✅ 找到 {len(results)} 个相关文档")

        return results

    except Exception as e:
        logger.error(f"❌ 搜索失败: {e}")
        raise