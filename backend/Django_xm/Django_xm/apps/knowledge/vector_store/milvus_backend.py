"""Milvus 向量存储后端实现"""

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from .base import VectorStoreBackend

logger = logging.getLogger(__name__)


class MilvusBackend(VectorStoreBackend):
    """Milvus 向量存储后端"""

    def __init__(self, uri: str = "milvus_demo.db"):
        self.uri = uri

    def _get_milvus_class(self):
        try:
            from langchain_milvus import Milvus

            return Milvus
        except ImportError:
            raise ImportError(
                "Milvus 未安装。请运行: pip install langchain-milvus"
            ) from None

    @property
    def store_type(self) -> str:
        return "milvus"

    def create(
        self,
        documents: list[Document],
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        Milvus = self._get_milvus_class()

        connection_args = kwargs.pop(
            "connection_args", {"uri": self.uri}
        )

        vector_store = Milvus.from_documents(
            documents=documents,
            embedding=embeddings,
            connection_args=connection_args,
            collection_name=collection_name,
            **kwargs,
        )
        logger.info(f"Milvus 向量库创建成功 (collection={collection_name})")
        return vector_store

    def load(
        self,
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        Milvus = self._get_milvus_class()

        connection_args = kwargs.pop(
            "connection_args", {"uri": self.uri}
        )

        vector_store = Milvus(
            embedding_function=embeddings,
            connection_args=connection_args,
            collection_name=collection_name,
            **kwargs,
        )
        logger.info(f"Milvus 向量库加载成功 (collection={collection_name})")
        return vector_store

    def save(
        self,
        vector_store: VectorStore,
        path: str,
        **kwargs: Any,
    ) -> None:
        """Milvus 自动持久化，此方法为空操作"""
        logger.debug("Milvus 自动持久化，无需手动保存")

    def delete(self, collection_name: str) -> bool:
        try:
            from pymilvus import connections, utility

            connections.connect(uri=self.uri)
            if utility.has_collection(collection_name):
                utility.drop_collection(collection_name)
                logger.info(f"Milvus 集合已删除: {collection_name}")
                return True
            logger.warning(f"Milvus 集合不存在: {collection_name}")
            return False
        except Exception as e:
            logger.error(f"Milvus 集合删除失败: {e}")
            return False

    def list_collections(self, prefix: str = "") -> list[str]:
        try:
            from pymilvus import connections, utility

            connections.connect(uri=self.uri)
            names = utility.list_collections()
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return names
        except Exception as e:
            logger.warning(f"列出 Milvus 集合失败: {e}")
            return []

    def exists(self, collection_name: str) -> bool:
        try:
            from pymilvus import connections, utility

            connections.connect(uri=self.uri)
            return utility.has_collection(collection_name)
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
            raise ValueError("Milvus 向量库不支持添加文档")
        logger.info(f"Milvus 添加 {len(ids)} 个文档")
        return ids

    def remove_documents(
        self,
        collection_name: str,
        document_ids: list[str],
    ) -> bool:
        try:
            from pymilvus import Collection, connections

            connections.connect(uri=self.uri)
            collection = Collection(collection_name)
            expr = f'id in {document_ids}'
            collection.delete(expr)
            logger.info(
                f"Milvus 从集合 {collection_name} 删除 {len(document_ids)} 个文档"
            )
            return True
        except Exception as e:
            logger.error(f"Milvus 删除文档失败: {e}")
            return False

    def remove_documents_by_metadata(
        self,
        collection_name: str,
        key: str,
        value: str,
    ) -> int:
        try:
            from pymilvus import Collection, connections

            connections.connect(uri=self.uri)
            collection = Collection(collection_name)
            expr = f'{key} == "{value}"'
            collection.delete(expr)
            logger.info(
                f"Milvus 按元数据删除文档 "
                f"(collection={collection_name}, {key}={value})"
            )
            return 1
        except Exception as e:
            logger.error(f"Milvus 按元数据删除失败: {e}")
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
            from pymilvus import Collection, connections, utility

            connections.connect(uri=self.uri)
            if not utility.has_collection(collection_name):
                return {
                    "name": collection_name,
                    "exists": False,
                    "store_type": "milvus",
                }

            collection = Collection(collection_name)
            return {
                "name": collection_name,
                "exists": True,
                "store_type": "milvus",
                "num_documents": collection.num_entities,
            }
        except Exception:
            return {
                "name": collection_name,
                "exists": False,
                "store_type": "milvus",
            }
