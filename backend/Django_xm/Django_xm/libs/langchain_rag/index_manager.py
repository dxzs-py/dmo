"""
索引管理器模块

提供向量索引的统一管理接口，包括：
- 索引的创建、加载、保存、删除
- 索引元数据管理
- 索引列表和统计
"""

import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from ..langchain_core.config import settings, get_logger


logger = get_logger(__name__)


def create_vector_store(
    documents: List[Document],
    embeddings: Embeddings,
    store_type: Optional[str] = None,
    **kwargs,
) -> VectorStore:
    """
    从文档创建向量存储
    """
    if not documents:
        raise ValueError("文档列表不能为空")

    store_type = store_type or settings.vector_store_type

    logger.info(f"🗄️  创建向量存储: type={store_type}, documents={len(documents)}")

    try:
        from langchain_community.vectorstores import FAISS

        if store_type == "faiss":
            vector_store = FAISS.from_documents(
                documents=documents,
                embedding=embeddings,
                **kwargs,
            )
            logger.info("✅ FAISS 向量库创建成功")
            return vector_store
        else:
            raise ValueError(f"不支持的向量库类型: {store_type}")

    except Exception as e:
        logger.error(f"❌ 创建向量库失败: {e}")
        raise


def save_vector_store(
    vector_store: VectorStore,
    save_path: str,
    embeddings: Optional[Embeddings] = None,
) -> None:
    """
    保存向量存储到磁盘
    """
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"💾 保存向量库: {save_path}")

    try:
        if hasattr(vector_store, 'save_local'):
            vector_store.save_local(str(save_path))
            logger.info("✅ 向量库保存成功")
        else:
            raise ValueError(f"向量库类型不支持保存: {type(vector_store)}")

    except Exception as e:
        logger.error(f"❌ 保存向量库失败: {e}")
        raise


def load_vector_store(
    load_path: str,
    embeddings: Embeddings,
    store_type: Optional[str] = None,
    **kwargs,
) -> VectorStore:
    """
    从磁盘加载向量存储
    """
    load_path = Path(load_path)

    if not load_path.exists():
        raise FileNotFoundError(f"向量库路径不存在: {load_path}")

    store_type = store_type or settings.vector_store_type

    logger.info(f"📂 加载向量库: {load_path}")

    try:
        from langchain_community.vectorstores import FAISS

        if store_type == "faiss":
            vector_store = FAISS.load_local(
                folder_path=str(load_path),
                embeddings=embeddings,
                allow_dangerous_deserialization=True,
                **kwargs,
            )
            logger.info("✅ FAISS 向量库加载成功")
            return vector_store
        else:
            raise ValueError(f"不支持的向量库类型: {store_type}")

    except Exception as e:
        logger.error(f"❌ 加载向量库失败: {e}")
        raise


def add_documents_to_vector_store(
    vector_store: VectorStore,
    documents: List[Document],
) -> None:
    """
    向现有向量库添加文档
    """
    if not documents:
        logger.warning("文档列表为空，无需添加")
        return

    logger.info(f"➕ 向向量库添加文档: {len(documents)} 个")

    try:
        vector_store.add_documents(documents)
        logger.info("✅ 文档添加成功")

    except Exception as e:
        logger.error(f"❌ 添加文档失败: {e}")
        raise


def search_vector_store(
    vector_store: VectorStore,
    query: str,
    k: int = 4,
    score_threshold: Optional[float] = None,
) -> List[Tuple[Document, float]]:
    """
    在向量库中搜索相似文档
    """
    logger.info(f"🔍 搜索向量库: query='{query[:50]}...', k={k}")

    try:
        results = vector_store.similarity_search_with_score(query, k=k)

        if score_threshold is not None:
            results = [(doc, score) for doc, score in results if score <= score_threshold]

        logger.info(f"✅ 找到 {len(results)} 个文档")
        return results

    except Exception as e:
        logger.error(f"❌ 搜索向量库失败: {e}")
        raise


def delete_vector_store(load_path: str) -> None:
    """
    删除向量库
    """
    import shutil

    load_path = Path(load_path)

    if not load_path.exists():
        logger.warning(f"向量库路径不存在: {load_path}")
        return

    try:
        shutil.rmtree(load_path)
        logger.info(f"✅ 向量库删除成功: {load_path}")

    except Exception as e:
        logger.error(f"❌ 删除向量库失败: {e}")
        raise


def get_vector_store_stats(vector_store: VectorStore) -> Dict[str, Any]:
    """
    获取向量库统计信息
    """
    try:
        doc_count = len(vector_store.docstore._dict) if hasattr(vector_store, 'docstore') else 0
        return {
            "document_count": doc_count,
        }
    except Exception as e:
        logger.error(f"❌ 获取向量库统计失败: {e}")
        return {"document_count": 0, "error": str(e)}


class IndexManager:
    """
    索引管理器

    管理所有向量索引的生命周期，包括创建、加载、更新、删除等操作.
    """

    def __init__(self, base_path: Optional[str] = None):
        self.base_path = Path(base_path or settings.vector_store_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"📁 索引管理器初始化: {self.base_path}")

    def _get_index_path(self, name: str) -> Path:
        return self.base_path / name

    def _get_metadata_path(self, name: str) -> Path:
        return self._get_index_path(name) / "metadata.json"

    def _save_metadata(self, name: str, metadata: Dict[str, Any]) -> None:
        metadata_path = self._get_metadata_path(name)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        logger.debug(f"💾 保存元数据: {metadata_path}")

    def _load_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        metadata_path = self._get_metadata_path(name)

        if not metadata_path.exists():
            return None

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            return metadata
        except Exception as e:
            logger.error(f"❌ 加载元数据失败: {e}")
            return None

    def create_index(
        self,
        name: str,
        documents: List[Document],
        embeddings: Embeddings,
        description: str = "",
        store_type: Optional[str] = None,
        overwrite: bool = False,
        **kwargs,
    ) -> VectorStore:
        """创建新索引"""
        index_path = self._get_index_path(name)

        if index_path.exists() and not overwrite:
            raise ValueError(f"索引已存在: {name}。使用 overwrite=True 来覆盖。")

        logger.info(f"🔨 创建索引: {name}")
        logger.info(f"   文档数量: {len(documents)}")

        try:
            vector_store = create_vector_store(documents, embeddings, store_type, **kwargs)

            index_path = self._get_index_path(name)
            save_vector_store(vector_store, str(index_path), embeddings)

            metadata = {
                "name": name,
                "description": description,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "num_documents": len(documents),
                "store_type": store_type or settings.vector_store_type,
                "embedding_model": settings.embedding_model,
            }
            self._save_metadata(name, metadata)

            logger.info(f"✅ 索引创建成功: {name}")
            return vector_store

        except Exception as e:
            logger.error(f"❌ 创建索引失败: {e}")
            if index_path.exists():
                delete_vector_store(str(index_path))
            raise

    def load_index(
        self,
        name: str,
        embeddings: Embeddings,
        **kwargs,
    ) -> VectorStore:
        """加载索引"""
        index_path = self._get_index_path(name)

        if not index_path.exists():
            raise FileNotFoundError(f"索引不存在: {name}")

        logger.info(f"📂 加载索引: {name}")

        try:
            metadata = self._load_metadata(name)
            if metadata:
                logger.info(f"   描述: {metadata.get('description', 'N/A')}")
                logger.info(f"   文档数: {metadata.get('num_documents', 'N/A')}")

            vector_store = load_vector_store(str(index_path), embeddings, **kwargs)

            logger.info(f"✅ 索引加载成功: {name}")
            return vector_store

        except Exception as e:
            logger.error(f"❌ 加载索引失败: {e}")
            raise

    def update_index(
        self,
        name: str,
        documents: List[Document],
        embeddings: Embeddings,
        **kwargs,
    ) -> VectorStore:
        """更新索引（添加新文档）"""
        logger.info(f"🔄 更新索引: {name}")
        logger.info(f"   新增文档: {len(documents)}")

        try:
            vector_store = self.load_index(name, embeddings, **kwargs)

            if hasattr(vector_store, 'add_documents'):
                vector_store.add_documents(documents)

            index_path = self._get_index_path(name)
            save_vector_store(vector_store, str(index_path), embeddings)

            metadata = self._load_metadata(name) or {}
            metadata["updated_at"] = datetime.now().isoformat()
            metadata["num_documents"] = metadata.get("num_documents", 0) + len(documents)
            self._save_metadata(name, metadata)

            logger.info(f"✅ 索引更新成功: {name}")
            return vector_store

        except Exception as e:
            logger.error(f"❌ 更新索引失败: {e}")
            raise

    def delete_index(self, name: str) -> bool:
        """删除索引"""
        index_path = self._get_index_path(name)

        if not index_path.exists():
            logger.warning(f"⚠️ 索引不存在: {name}")
            return False

        try:
            delete_vector_store(str(index_path))
            logger.info(f"🗑️ 索引删除成功: {name}")
            return True
        except Exception as e:
            logger.error(f"❌ 删除索引失败: {e}")
            return False

    def index_exists(self, name: str) -> bool:
        """检查索引是否存在"""
        return self._get_index_path(name).exists()

    def list_indexes(self) -> List[Dict[str, Any]]:
        """列出所有索引"""
        indexes = []

        if not self.base_path.exists():
            return indexes

        for item in self.base_path.iterdir():
            if item.is_dir():
                metadata = self._load_metadata(item.name)
                if metadata:
                    indexes.append(metadata)
                else:
                    indexes.append({
                        "name": item.name,
                        "description": "",
                        "created_at": "",
                        "updated_at": "",
                        "num_documents": 0,
                    })

        return indexes

    def get_index_info(self, name: str) -> Optional[Dict[str, Any]]:
        """获取索引信息"""
        if not self.index_exists(name):
            return None

        metadata = self._load_metadata(name)
        if metadata:
            return metadata

        return {
            "name": name,
            "description": "",
            "created_at": "",
            "updated_at": "",
            "num_documents": 0,
        }

    def get_vector_store_stats(self, name: str) -> Dict[str, Any]:
        """获取向量存储统计信息"""
        try:
            vector_store = self.load_index(name, None)
            return get_vector_store_stats(vector_store)
        except Exception:
            return {"name": name, "index_size": 0}
