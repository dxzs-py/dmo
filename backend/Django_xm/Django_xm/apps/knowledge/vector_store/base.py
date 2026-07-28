"""向量存储后端抽象基类"""

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore


class VectorStoreBackend(ABC):
    """向量存储后端抽象基类

    所有向量存储后端必须实现此接口，以支持策略模式 + 注册表架构。
    """

    @abstractmethod
    def create(
        self,
        documents: list[Document],
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        """创建新的向量存储"""

    @abstractmethod
    def load(
        self,
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        """加载已有的向量存储"""

    @abstractmethod
    def save(
        self,
        vector_store: VectorStore,
        path: str,
        **kwargs: Any,
    ) -> None:
        """持久化向量存储（PGVector 自动持久化，此方法为空操作）"""

    @abstractmethod
    def delete(self, collection_name: str) -> bool:
        """删除向量存储"""

    @abstractmethod
    def list_collections(self, prefix: str = "") -> list[str]:
        """列出所有集合名"""

    @abstractmethod
    def exists(self, collection_name: str) -> bool:
        """检查集合是否存在"""

    @abstractmethod
    def add_documents(
        self,
        vector_store: VectorStore,
        documents: list[Document],
    ) -> list[str]:
        """向向量存储添加文档"""

    @abstractmethod
    def remove_documents(
        self,
        collection_name: str,
        document_ids: list[str],
    ) -> bool:
        """按 ID 删除文档"""

    @abstractmethod
    def remove_documents_by_metadata(
        self,
        collection_name: str,
        key: str,
        value: str,
    ) -> int:
        """按元数据删除文档，返回删除数量"""

    def remove_documents_by_metadata_like(
        self,
        collection_name: str,
        key: str,
        pattern: str,
    ) -> int:
        """按元数据 LIKE 模式删除文档，返回删除数量

        默认实现返回 0，仅 PGVector 等支持 SQL LIKE 的后端需要覆盖。
        """
        return 0

    def read_all_documents(
        self,
        collection_name: str,
    ) -> list[Document]:
        """读取集合中所有文档

        默认实现返回空列表，由具体后端覆盖。
        """
        return []

    @abstractmethod
    def search(
        self,
        vector_store: VectorStore,
        query: str,
        k: int = 4,
        filter: dict | None = None,
    ) -> list[tuple[Document, float]]:
        """相似度搜索"""

    @abstractmethod
    def get_stats(self, collection_name: str) -> dict[str, Any]:
        """获取存储统计信息"""

    @property
    @abstractmethod
    def store_type(self) -> str:
        """存储类型标识符"""
