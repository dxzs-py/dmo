"""
索引管理器模块
提供向量索引的统一管理接口
"""

import json
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from Django_xm.apps.ai_engine.config import settings, get_logger

logger = get_logger(__name__)


def _get_chroma_client_settings(persist_directory: str = None):
    """获取 Chroma 客户端配置，支持持久化"""
    try:
        import chromadb
        if persist_directory:
            return chromadb.Settings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=persist_directory,
                anonymized_telemetry=False,
            )
        return chromadb.Settings(anonymized_telemetry=False)
    except ImportError:
        return None


def create_vector_store(
    documents: List[Document],
    embeddings: Embeddings,
    store_type: Optional[str] = None,
    **kwargs,
) -> VectorStore:
    if not documents:
        raise ValueError("文档列表不能为空")

    store_type = store_type or settings.vector_store_type

    logger.info(f"创建向量存储: type={store_type}, documents={len(documents)}")

    try:
        if store_type == "faiss":
            from langchain_community.vectorstores import FAISS

            vector_store = FAISS.from_documents(
                documents=documents,
                embedding=embeddings,
                **kwargs,
            )
            logger.info("FAISS 向量库创建成功")
            return vector_store

        elif store_type == "chroma":
            try:
                import chromadb
                from langchain_chroma import Chroma
            except ImportError:
                raise ImportError(
                    "Chroma 未安装。请运行: pip install langchain-chroma chromadb"
                )

            persist_directory = kwargs.pop("persist_directory", None)
            collection_name = kwargs.pop("collection_name", "default")

            client_settings = _get_chroma_client_settings(persist_directory)

            chroma_kwargs = {
                "collection_name": collection_name,
                "embedding_function": embeddings,
            }
            if persist_directory:
                chroma_kwargs["persist_directory"] = persist_directory
            if client_settings:
                chroma_kwargs["client_settings"] = client_settings

            vector_store = Chroma.from_documents(
                documents=documents,
                **chroma_kwargs,
            )
            logger.info(f"Chroma 向量库创建成功 (persist={bool(persist_directory)})")
            return vector_store

        elif store_type == "inmemory":
            from langchain_core.vectorstores import InMemoryVectorStore

            vector_store = InMemoryVectorStore.from_documents(
                documents=documents,
                embedding=embeddings,
                **kwargs,
            )
            logger.info("InMemory 向量库创建成功")
            return vector_store

        else:
            raise ValueError(
                f"不支持的向量库类型: {store_type}。"
                f"支持的类型: faiss, chroma, inmemory"
            )

    except Exception as e:
        logger.error(f"创建向量库失败: {e}")
        raise


def save_vector_store(vector_store: VectorStore, save_path: str, embeddings: Optional[Embeddings] = None) -> None:
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"保存向量库: {save_path}")

    try:
        if hasattr(vector_store, 'save_local'):
            vector_store.save_local(str(save_path))
            logger.info("向量库保存成功")
        else:
            vs_type = type(vector_store).__name__
            if vs_type == "Chroma":
                if hasattr(vector_store, '_persist'):
                    vector_store._persist()
                logger.info("Chroma 向量库已自动持久化")
            else:
                logger.warning(f"向量库类型 {vs_type} 不支持手动保存")
    except Exception as e:
        logger.error(f"保存向量库失败: {e}")
        raise


def load_vector_store(
    load_path: str,
    embeddings: Embeddings,
    store_type: Optional[str] = None,
    **kwargs,
) -> VectorStore:
    load_path = Path(load_path)

    store_type = store_type or settings.vector_store_type

    logger.info(f"加载向量库: {load_path}, type={store_type}")

    try:
        if store_type == "faiss":
            if not load_path.exists():
                raise FileNotFoundError(f"向量库路径不存在: {load_path}")

            from langchain_community.vectorstores import FAISS

            vector_store = FAISS.load_local(
                folder_path=str(load_path),
                embeddings=embeddings,
                allow_dangerous_deserialization=True,
                **kwargs,
            )
            logger.info("FAISS 向量库加载成功")
            return vector_store

        elif store_type == "chroma":
            try:
                from langchain_chroma import Chroma
            except ImportError:
                raise ImportError(
                    "Chroma 未安装。请运行: pip install langchain-chroma chromadb"
                )

            if not load_path.exists():
                raise FileNotFoundError(f"向量库路径不存在: {load_path}")

            collection_name = kwargs.pop("collection_name", "default")
            client_settings = _get_chroma_client_settings(str(load_path))

            chroma_kwargs = {
                "collection_name": collection_name,
                "embedding_function": embeddings,
                "persist_directory": str(load_path),
            }
            if client_settings:
                chroma_kwargs["client_settings"] = client_settings

            vector_store = Chroma(**chroma_kwargs)
            logger.info("Chroma 向量库加载成功")
            return vector_store

        elif store_type == "inmemory":
            raise ValueError("InMemoryVectorStore 不支持从磁盘加载")

        else:
            raise ValueError(
                f"不支持的向量库类型: {store_type}。"
                f"支持的类型: faiss, chroma"
            )

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
        documents: Optional[List[Document]] = None,
        embeddings: Optional[Embeddings] = None,
        description: str = "",
        store_type: Optional[str] = None,
        overwrite: bool = False,
        **kwargs,
    ):
        """创建新索引，支持创建空索引"""
        index_path = self._get_index_path(name)

        if index_path.exists() and not overwrite:
            raise ValueError(f"索引已存在: {name}。使用 overwrite=True 来覆盖。")

        logger.info(f"创建索引: {name}，文档数量: {len(documents) if documents else 0}")

        try:
            vector_store = None
            if documents and embeddings:
                vector_store = create_vector_store(documents, embeddings, store_type, **kwargs)
                save_vector_store(vector_store, str(index_path), embeddings)

            from datetime import datetime
            metadata = {
                "name": name,
                "description": description,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "num_documents": len(documents) if documents else 0,
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

    def create_empty_index(
        self,
        name: str,
        description: str = "",
        store_type: Optional[str] = None,
        overwrite: bool = False,
    ):
        """创建空索引（无需指定文档）"""
        return self.create_index(
            name=name,
            documents=None,
            embeddings=None,
            description=description,
            store_type=store_type,
            overwrite=overwrite,
        )

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
            # 检查是否为空索引（只有 metadata 文件）
            metadata = self._load_metadata(name)
            is_empty_index = metadata and metadata.get("num_documents", 0) == 0
            
            if is_empty_index:
                # 如果是空索引，直接创建新的向量库
                logger.info(f"索引 {name} 为空，创建新的向量库")
                vector_store = create_vector_store(documents, embeddings)
                save_vector_store(vector_store, str(index_path), embeddings)
            else:
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
            if metadata:
                old_count = metadata.get("num_documents", 0)
                metadata["num_documents"] = old_count + len(documents)
                metadata["updated_at"] = datetime.now().isoformat()
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

    def remove_documents_by_filename(
        self,
        name: str,
        embeddings: Embeddings,
        filename: str,
    ) -> int:
        """
        根据文件名从索引中删除所有相关文档

        Args:
            name: 索引名称
            embeddings: Embedding 模型
            filename: 要删除的文件名

        Returns:
            成功删除的文档数量
        """
        index_path = self._get_index_path(name)

        if not index_path.exists():
            raise FileNotFoundError(f"索引不存在：{name}")

        logger.info(f"从索引 {name} 按文件名删除文档: {filename}")

        try:
            vector_store = self.load_index(name, embeddings)
            
            # 打印索引中的文档数量
            if hasattr(vector_store, 'index_to_docstore_id'):
                total_docs = len(vector_store.index_to_docstore_id)
                logger.info(f"索引 {name} 中共有 {total_docs} 个文档块")

            ids_to_delete = []
            debug_matched = []

            if hasattr(vector_store, 'docstore') and hasattr(vector_store, 'index_to_docstore_id'):
                index_to_id = vector_store.index_to_docstore_id
                for idx, doc_id in index_to_id.items():
                    try:
                        doc = vector_store.docstore.search(doc_id)
                        if doc is not None:
                            doc_source = ""
                            doc_file_name = ""
                            if isinstance(doc, Document):
                                doc_source = doc.metadata.get('source', '')
                                doc_file_name = doc.metadata.get('file_name', '')
                                
                                # 添加调试日志
                                if idx < 5:  # 只打印前5个文档的信息
                                    logger.debug(f"文档 {doc_id}: file_name={doc_file_name}, source={doc_source}")
                            elif isinstance(doc, str):
                                continue

                            if (doc_file_name == filename
                                    or doc_source.endswith(filename)
                                    or (filename in doc_source)):
                                ids_to_delete.append(doc_id)
                                debug_matched.append((doc_id, doc_file_name, doc_source))
                    except Exception as e:
                        logger.warning(f"查找文档 {doc_id} 失败: {e}")
                        continue

            logger.info(f"找到 {len(ids_to_delete)} 个匹配的文档块: {debug_matched}")

            if not ids_to_delete:
                logger.warning(f"未在索引 {name} 中找到文件 {filename} 的文档")
                return 0

            if hasattr(vector_store, 'delete'):
                logger.info(f"调用 vector_store.delete 删除 {len(ids_to_delete)} 个文档")
                vector_store.delete(ids_to_delete)
            else:
                logger.warning("向量库不支持删除操作")
                return 0

            save_vector_store(vector_store, str(index_path), embeddings)

            metadata = self._load_metadata(name)
            if metadata:
                old_count = metadata.get("num_documents", 0)
                metadata["num_documents"] = max(0, old_count - len(ids_to_delete))
                metadata["updated_at"] = datetime.now().isoformat()
                self._save_metadata(name, metadata)

            logger.info(f"成功从索引 {name} 删除文件 {filename} 的 {len(ids_to_delete)} 个文档块")
            return len(ids_to_delete)

        except Exception as e:
            logger.error(f"按文件名删除文档失败：{e}")
            raise