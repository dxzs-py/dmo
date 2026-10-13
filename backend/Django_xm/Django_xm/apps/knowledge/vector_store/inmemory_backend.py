"""InMemory 向量存储后端实现

基于内存的向量存储，不支持持久化，适用于测试和临时场景。
"""

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from .base import VectorStoreBackend

logger = logging.getLogger(__name__)


class InMemoryBackend(VectorStoreBackend):
    """InMemory 向量存储后端"""

    def __init__(self):
        self._stores: dict[str, VectorStore] = {}

    @property
    def store_type(self) -> str:
        return "inmemory"

    def _get_inmemory_class(self):
        from langchain_core.vectorstores import InMemoryVectorStore

        return InMemoryVectorStore

    def create(
        self,
        documents: list[Document],
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        InMemoryVectorStore = self._get_inmemory_class()

        vector_store = InMemoryVectorStore.from_documents(
            documents=documents,
            embedding=embeddings,
            **kwargs,
        )
        self._stores[collection_name] = self._stamp_score_semantics(vector_store)
        logger.info(f"InMemory 向量库创建成功 (collection={collection_name})")
        return self._stores[collection_name]

    def load(
        self,
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        if collection_name in self._stores:
            return self._stores[collection_name]
        raise ValueError(
            f"InMemory 向量库 '{collection_name}' 不存在或已被清除。InMemoryVectorStore 不支持从磁盘加载。"
        )

    def save(
        self,
        vector_store: VectorStore,
        path: str,
        **kwargs: Any,
    ) -> None:
        """InMemory 不支持持久化，此方法为空操作"""
        logger.debug("InMemory 向量库不支持持久化")

    def delete(self, collection_name: str) -> bool:
        if collection_name in self._stores:
            del self._stores[collection_name]
            logger.info(f"InMemory 向量库已删除: {collection_name}")
            return True
        logger.warning(f"InMemory 向量库不存在: {collection_name}")
        return False

    def list_collections(self, prefix: str = "") -> list[str]:
        names = list(self._stores.keys())
        if prefix:
            names = [n for n in names if n.startswith(prefix)]
        return names

    def exists(self, collection_name: str) -> bool:
        return collection_name in self._stores

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
            raise ValueError("InMemory 向量库不支持添加文档")
        logger.info(f"InMemory 添加 {len(ids)} 个文档")
        return ids

    def remove_documents(
        self,
        collection_name: str,
        document_ids: list[str],
    ) -> bool:
        """按 ID 删除文档（真实实现，3.3 去假）。

        进程内 store 直接支持删除（InMemoryVectorStore.delete），
        旧实现返回 False 纯属假失败。
        """
        vector_store = self._stores.get(collection_name)
        if vector_store is None:
            logger.warning(f"InMemory 向量库不存在: {collection_name}")
            return False
        if not document_ids:
            return True
        vector_store.delete(document_ids)
        logger.info(f"InMemory 按 ID 删除 {len(document_ids)} 个文档 (collection={collection_name})")
        return True

    def remove_documents_by_metadata(
        self,
        collection_name: str,
        key: str,
        value: str,
    ) -> int:
        """按元数据删除文档，返回删除数量（真实实现，3.3 去假）。

        遍历内存 store 匹配 metadata[key] == value 后按 ID 删除；
        旧实现恒返 0 纯属假失败。

        langchain-core 1.5.4 实测：``InMemoryVectorStore.store`` 为
        ``{doc_id: {"id", "vector", "text", "metadata"}}`` dict 条目
        （非 Document 对象），metadata 经 entry["metadata"] 取。
        """
        vector_store = self._stores.get(collection_name)
        if vector_store is None:
            logger.warning(f"InMemory 向量库不存在: {collection_name}")
            return 0
        entries = getattr(vector_store, "store", None) or {}
        ids_to_delete = [
            doc_id
            for doc_id, entry in entries.items()
            if isinstance(entry, dict) and isinstance(entry.get("metadata"), dict)
            and entry["metadata"].get(key) == value
        ]
        if not ids_to_delete:
            logger.info(f"InMemory 集合 {collection_name} 中未找到 metadata[{key}]={value} 的文档")
            return 0
        vector_store.delete(ids_to_delete)
        logger.info(
            f"InMemory 按元数据删除 {len(ids_to_delete)} 个文档 "
            f"(collection={collection_name}, {key}={value})"
        )
        return len(ids_to_delete)

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
        """存储统计信息（num_documents 为真实计数，3.3 去掉恒 0 假值）"""
        vector_store = self._stores.get(collection_name)
        if vector_store is not None:
            return {
                "name": collection_name,
                "exists": True,
                "store_type": "inmemory",
                # InMemoryVectorStore.store 为 {doc_id: Document} 内存字典，
                # len 即真实文档数（无滞后）
                "num_documents": len(getattr(vector_store, "store", {}) or {}),
            }
        return {
            "name": collection_name,
            "exists": False,
            "store_type": "inmemory",
        }
