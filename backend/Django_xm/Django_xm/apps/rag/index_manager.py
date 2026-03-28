"""
索引管理器模块
提供向量索引的统一管理接口
"""

import json
from pathlib import Path
from typing import List, Optional, Dict, Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from Django_xm.apps.core.config import settings, get_logger

logger = get_logger(__name__)


def create_vector_store(
    documents: List[Document],
    embeddings: Embeddings,
    store_type: Optional[str] = None,
    **kwargs,
) -> VectorStore:
    """从文档创建向量存储"""
    if not documents:
        raise ValueError("文档列表不能为空")

    store_type = store_type or settings.vector_store_type

    logger.info(f"创建向量存储: type={store_type}, documents={len(documents)}")

    try:
        from langchain_community.vectorstores import FAISS

        if store_type == "faiss":
            vector_store = FAISS.from_documents(
                documents=documents,
                embedding=embeddings,
                **kwargs,
            )
            logger.info("FAISS 向量库创建成功")
            return vector_store
        else:
            raise ValueError(f"不支持的向量库类型: {store_type}")

    except Exception as e:
        logger.error(f"创建向量库失败: {e}")
        raise


def save_vector_store(vector_store: VectorStore, save_path: str, embeddings: Optional[Embeddings] = None) -> None:
    """保存向量存储到磁盘"""
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"保存向量库: {save_path}")

    try:
        if hasattr(vector_store, 'save_local'):
            vector_store.save_local(str(save_path))
            logger.info("向量库保存成功")
        else:
            raise ValueError(f"向量库类型不支持保存: {type(vector_store)}")
    except Exception as e:
        logger.error(f"保存向量库失败: {e}")
        raise


def load_vector_store(
    load_path: str,
    embeddings: Embeddings,
    store_type: Optional[str] = None,
    **kwargs,
) -> VectorStore:
    """从磁盘加载向量存储"""
    load_path = Path(load_path)

    if not load_path.exists():
        raise FileNotFoundError(f"向量库路径不存在: {load_path}")

    store_type = store_type or settings.vector_store_type

    logger.info(f"加载向量库: {load_path}")

    try:
        from langchain_community.vectorstores import FAISS

        if store_type == "faiss":
            vector_store = FAISS.load_local(
                folder_path=str(load_path),
                embeddings=embeddings,
                allow_dangerous_deserialization=True,
                **kwargs,
            )
            logger.info("FAISS 向量库加载成功")
            return vector_store
        else:
            raise ValueError(f"不支持的向量库类型: {store_type}")

    except Exception as e:
        logger.error(f"加载向量库失败: {e}")
        raise


class IndexManager:
    """索引管理器"""

    def __init__(self, base_path: Optional[str] = None):
        self.base_path = Path(base_path or settings.vector_store_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"索引管理器初始化: {self.base_path}")

    def _get_index_path(self, name: str) -> Path:
        return self.base_path / name

    def _get_metadata_path(self, name: str) -> Path:
        return self._get_index_path(name) / "metadata.json"

    def _save_metadata(self, name: str, metadata: Dict[str, Any]) -> None:
        metadata_path = self._get_metadata_path(name)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    def _load_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        metadata_path = self._get_metadata_path(name)
        if not metadata_path.exists():
            return None
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载元数据失败: {e}")
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
    ):
        """创建新索引"""
        index_path = self._get_index_path(name)

        if index_path.exists() and not overwrite:
            raise ValueError(f"索引已存在: {name}。使用 overwrite=True 来覆盖。")

        logger.info(f"创建索引: {name}，文档数量: {len(documents)}")

        try:
            vector_store = create_vector_store(documents, embeddings, store_type, **kwargs)
            index_path = self._get_index_path(name)
            save_vector_store(vector_store, str(index_path), embeddings)

            from datetime import datetime
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

            logger.info(f"索引创建成功: {name}")
            return vector_store

        except Exception as e:
            logger.error(f"创建索引失败: {e}")
            if index_path.exists():
                import shutil
                shutil.rmtree(index_path)
            raise

    def load_index(self, name: str, embeddings: Embeddings, **kwargs):
        """加载索引"""
        index_path = self._get_index_path(name)

        if not index_path.exists():
            raise FileNotFoundError(f"索引不存在: {name}")

        logger.info(f"加载索引: {name}")

        try:
            metadata = self._load_metadata(name)
            if metadata:
                logger.info(f"描述: {metadata.get('description', 'N/A')}")
                logger.info(f"文档数: {metadata.get('num_documents', 'N/A')}")

            vector_store = load_vector_store(str(index_path), embeddings, **kwargs)
            logger.info(f"索引加载成功: {name}")
            return vector_store

        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            raise

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

    def delete_index(self, name: str) -> bool:
        """删除索引"""
        index_path = self._get_index_path(name)

        if not index_path.exists():
            logger.warning(f"索引不存在: {name}")
            return False

        try:
            import shutil
            shutil.rmtree(index_path)
            logger.info(f"索引删除成功: {name}")
            return True
        except Exception as e:
            logger.error(f"删除索引失败: {e}")
            return False

    def index_exists(self, name: str) -> bool:
        """检查索引是否存在"""
        return self._get_index_path(name).exists()

    def get_index_stats(self, name: str, embeddings: Embeddings = None) -> Dict[str, Any]:
        """
        获取索引统计信息
        
        Args:
            name: 索引名称
            embeddings: Embedding 模型（可选，用于获取更详细的统计）
            
        Returns:
            包含统计信息的字典
        """
        index_path = self._get_index_path(name)
        
        if not index_path.exists():
            raise FileNotFoundError(f"索引不存在：{name}")
        
        metadata = self._load_metadata(name)
        
        stats = {
            "name": name,
            "exists": True,
            "num_documents": metadata.get("num_documents", 0) if metadata else 0,
            "description": metadata.get("description", "") if metadata else "",
            "created_at": metadata.get("created_at", "") if metadata else "",
            "updated_at": metadata.get("updated_at", "") if metadata else "",
            "store_type": metadata.get("store_type", "faiss"),
            "embedding_model": metadata.get("embedding_model", ""),
        }
        
        # 如果有 embeddings，尝试加载向量库获取更详细信息
        if embeddings:
            try:
                vector_store = self.load_index(name, embeddings)
                if hasattr(vector_store, 'index') and vector_store.index:
                    stats["dimension"] = vector_store.index.d
                    stats["total_vectors"] = vector_store.index.ntotal
            except Exception as e:
                logger.warning(f"获取详细统计失败：{e}")
        
        return stats

    def add_documents(
        self,
        name: str,
        documents: List[Document],
        embeddings: Embeddings,
    ) -> int:
        """
        向现有索引添加文档
        
        Args:
            name: 索引名称
            documents: 要添加的文档列表
            embeddings: Embedding 模型
            
        Returns:
            成功添加的文档数量
        """
        index_path = self._get_index_path(name)
        
        if not index_path.exists():
            raise FileNotFoundError(f"索引不存在：{name}")
        
        if not documents:
            raise ValueError("文档列表不能为空")
        
        logger.info(f"向索引 {name} 添加 {len(documents)} 个文档")
        
        try:
            # 加载现有向量库
            vector_store = self.load_index(name, embeddings)
            
            # 添加文档
            if hasattr(vector_store, 'add_documents'):
                vector_store.add_documents(documents)
            elif hasattr(vector_store, 'add_texts'):
                texts = [doc.page_content for doc in documents]
                metadatas = [doc.metadata for doc in documents]
                vector_store.add_texts(texts, metadatas)
            else:
                raise ValueError("向量库不支持添加文档")
            
            # 保存更新后的向量库
            save_vector_store(vector_store, str(index_path), embeddings)
            
            # 更新元数据
            metadata = self._load_metadata(name)
            if metadata:
                old_count = metadata.get("num_documents", 0)
                metadata["num_documents"] = old_count + len(documents)
                metadata["updated_at"] = __import__('datetime').datetime.now().isoformat()
                self._save_metadata(name, metadata)
            
            logger.info(f"成功添加 {len(documents)} 个文档到索引 {name}")
            return len(documents)
            
        except Exception as e:
            logger.error(f"添加文档失败：{e}")
            raise

    def remove_documents(
        self,
        name: str,
        embeddings: Embeddings,
        document_ids: Optional[List[str]] = None,
    ) -> int:
        """
        从索引中删除文档
        
        Args:
            name: 索引名称
            embeddings: Embedding 模型
            document_ids: 要删除的文档 ID 列表
            
        Returns:
            成功删除的文档数量
        """
        index_path = self._get_index_path(name)
        
        if not index_path.exists():
            raise FileNotFoundError(f"索引不存在：{name}")
        
        logger.info(f"从索引 {name} 删除文档")
        
        try:
            # 加载现有向量库
            vector_store = self.load_index(name, embeddings)
            
            # 删除文档（如果支持）
            if hasattr(vector_store, 'delete') and document_ids:
                vector_store.delete(document_ids)
            else:
                logger.warning("向量库不支持删除操作")
                return 0
            
            # 保存更新后的向量库
            save_vector_store(vector_store, str(index_path), embeddings)
            
            logger.info(f"成功删除文档 from 索引 {name}")
            return len(document_ids) if document_ids else 0
            
        except Exception as e:
            logger.error(f"删除文档失败：{e}")
            raise