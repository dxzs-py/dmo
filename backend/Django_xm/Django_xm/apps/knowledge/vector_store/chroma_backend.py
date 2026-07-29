"""Chroma 向量存储后端实现"""

import logging
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from .base import VectorStoreBackend

logger = logging.getLogger(__name__)


def _get_chroma_client_settings(persist_directory: str | None = None):
    """获取 Chroma 客户端配置"""
    try:
        import chromadb

        if persist_directory:
            return chromadb.Settings(
                persist_directory=persist_directory,
                anonymized_telemetry=False,
            )
        return chromadb.Settings(anonymized_telemetry=False)
    except ImportError:
        return None


class ChromaBackend(VectorStoreBackend):
    """Chroma 向量存储后端"""

    def __init__(
        self,
        persist_directory: str = "data/chroma_db",
        collection_name: str = "langchain_xm",
    ):
        self.persist_directory = persist_directory
        self.default_collection_name = collection_name

    def _get_chroma_class(self):
        try:
            from langchain_chroma import Chroma

            return Chroma
        except ImportError:
            raise ImportError("Chroma 未安装。请运行: pip install langchain-chroma chromadb") from None

    @property
    def store_type(self) -> str:
        return "chroma"

    def create(
        self,
        documents: list[Document],
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        Chroma = self._get_chroma_class()

        persist_directory = kwargs.pop("persist_directory", self.persist_directory)
        client_settings = _get_chroma_client_settings(persist_directory)

        chroma_kwargs: dict[str, Any] = {
            "collection_name": collection_name,
            "embedding_function": embeddings,
            "persist_directory": persist_directory,
        }
        if client_settings:
            chroma_kwargs["client_settings"] = client_settings

        vector_store = Chroma.from_documents(
            documents=documents,
            **chroma_kwargs,
        )
        logger.info(f"Chroma 向量库创建成功 (persist={persist_directory}, collection={collection_name})")
        return vector_store

    def load(
        self,
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        Chroma = self._get_chroma_class()

        persist_directory = kwargs.pop("persist_directory", self.persist_directory)
        load_path = kwargs.pop("load_path", persist_directory)

        if not Path(load_path).exists():
            raise FileNotFoundError(f"Chroma 向量库路径不存在: {load_path}")

        client_settings = _get_chroma_client_settings(str(load_path))

        chroma_kwargs: dict[str, Any] = {
            "collection_name": collection_name,
            "embedding_function": embeddings,
            "persist_directory": str(load_path),
        }
        if client_settings:
            chroma_kwargs["client_settings"] = client_settings

        vector_store = Chroma(**chroma_kwargs)
        logger.info(f"Chroma 向量库加载成功 (collection={collection_name})")
        return vector_store

    def save(
        self,
        vector_store: VectorStore,
        path: str,
        **kwargs: Any,
    ) -> None:
        if hasattr(vector_store, "_persist"):
            vector_store._persist()
        logger.info("Chroma 向量库已持久化")

    def delete(self, collection_name: str) -> bool:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=self.persist_directory)
            client.delete_collection(name=collection_name)
            logger.info(f"Chroma 集合已删除: {collection_name}")
            return True
        except Exception:
            logger.exception("Chroma 集合删除失败")
            return False

    def list_collections(self, prefix: str = "") -> list[str]:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=self.persist_directory)
            collections = client.list_collections()
            names = [c.name for c in collections]
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return names
        except Exception as e:
            logger.warning(f"列出 Chroma 集合失败: {e}")
            return []

    def exists(self, collection_name: str) -> bool:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=self.persist_directory)
            try:
                client.get_collection(name=collection_name)
                return True
            except Exception:
                return False
        except Exception:
            return False

    def add_documents(
        self,
        vector_store: VectorStore,
        documents: list[Document],
    ) -> list[str]:
        if hasattr(vector_store, "add_documents"):
            ids = vector_store.add_documents(documents)
        elif hasattr(vector_store, "add_texts"):
            texts = [doc.page_content for doc in documents]
            metadatas = [doc.metadata for doc in documents]
            ids = vector_store.add_texts(texts, metadatas)
        else:
            raise ValueError("Chroma 向量库不支持添加文档")
        logger.info(f"Chroma 添加 {len(ids)} 个文档")
        return ids

    def remove_documents(
        self,
        collection_name: str,
        document_ids: list[str],
    ) -> bool:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=self.persist_directory)
            collection = client.get_collection(name=collection_name)
            collection.delete(ids=document_ids)
            logger.info(f"Chroma 从集合 {collection_name} 删除 {len(document_ids)} 个文档")
            return True
        except Exception:
            logger.exception("Chroma 删除文档失败")
            return False

    def remove_documents_by_metadata(
        self,
        collection_name: str,
        key: str,
        value: str,
    ) -> int:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=self.persist_directory)
            collection = client.get_collection(name=collection_name)
            result = collection.get(where={key: value})
            ids_to_delete = result.get("ids", [])
            if ids_to_delete:
                collection.delete(ids=ids_to_delete)
            logger.info(
                f"Chroma 按元数据删除 {len(ids_to_delete)} 个文档 (collection={collection_name}, {key}={value})"
            )
            return len(ids_to_delete)
        except Exception:
            logger.exception("Chroma 按元数据删除失败")
            return 0

    def search(
        self,
        vector_store: VectorStore,
        query: str,
        k: int = 4,
        filter: dict | None = None,
    ) -> list[tuple[Document, float]]:
        kwargs: dict[str, Any] = {"k": k}
        if filter:
            kwargs["filter"] = filter
        return vector_store.similarity_search_with_score(query=query, **kwargs)

    def get_stats(self, collection_name: str) -> dict[str, Any]:
        try:
            import chromadb

            client = chromadb.PersistentClient(path=self.persist_directory)
            collection = client.get_collection(name=collection_name)
            count = collection.count()
            return {
                "name": collection_name,
                "exists": True,
                "store_type": "chroma",
                "num_documents": count,
            }
        except Exception:
            return {
                "name": collection_name,
                "exists": False,
                "store_type": "chroma",
            }
