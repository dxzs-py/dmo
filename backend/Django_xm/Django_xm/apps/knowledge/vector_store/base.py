"""向量存储后端抽象基类"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

logger = logging.getLogger(__name__)


class VectorStoreBackend(ABC):
    """向量存储后端抽象基类

    所有向量存储后端必须实现此接口，以支持策略模式 + 注册表架构。

    后端能力声明（C-2arch 收敛，2026-10-06）：
    - ``native_score_semantics``：本后端**底层 store 实例**相似度检索返回分数
      的原生语义。pgvector 的 langchain_postgres store 返回 cosine distance
      （越小越相关）→ 声明 "distance"；其余后端返回 similarity（越大越相关）
      → 声明 "similarity"（默认）。调用方（search_scored / kb_service /
      views_rag）经 ``IndexManager.get_score_semantics()`` 取用，**不得**再按
      store_type 字符串自行硬判——新增返回 distance 语义的后端只需覆写此属性。
      注意与后端级 ``search()`` 的归一输出是两层不同契约（3.4）。

    后端能力矩阵（3.3 假删除显式化，2026-10-07）：
    - ``supports_remove``：是否支持按 ID / 按元数据删除文档。不支持的后端
      （faiss）声明 False——``IndexManager.remove_documents`` 据此直接走
      load → delete → save 通用回退；绕过管理器直接调用的消费方收到
      ``NotImplementedError``（禁止返回 False/0 假成功）。
    - ``supports_stats``：``get_stats`` 是否提供真实文档计数。声明 False 的
      后端 ``num_documents`` 可能缺省/滞后，消费方不得据此做删除成功判定。
    """

    #: 后端原生分数语义（"similarity" | "distance"），见类 docstring
    native_score_semantics: str = "similarity"

    #: 能力矩阵：是否支持文档级删除（按 ID / 按元数据），见类 docstring
    supports_remove: bool = True

    #: 能力矩阵：get_stats 是否提供真实文档计数，见类 docstring
    supports_stats: bool = True

    def _stamp_score_semantics(self, vector_store: VectorStore) -> VectorStore:
        """把后端声明的分数语义烙到 store 实例上。

        create/load 返回的是原生 langchain store（如 langchain_postgres.PGVector），
        不携带后端类上的 ``native_score_semantics`` 声明——而鸭子类型消费方
        （rag_retrieval._initial_search_from_retriever 的 search_scored 门控）
        只拿得到 store 实例。冒烟实测：pgvector 路径声明丢失 → distance 分按
        similarity 解读（反向）。各后端 create/load 出口统一经本方法返回。
        """
        try:
            vector_store.native_score_semantics = self.native_score_semantics
        except Exception as e:  # 烙印失败不阻断建库（消费方回退 similarity 默认）
            logger.warning(
                f"烙印 native_score_semantics 失败（非致命）: {e}"
            )
        return vector_store

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

    def remove_documents(
        self,
        collection_name: str,
        document_ids: list[str],
    ) -> bool:
        """按 ID 删除文档

        默认实现抛 NotImplementedError：3.3 禁止"返回 False 静默假失败"——
        不支持文档级删除的后端（faiss，supports_remove=False）不再覆写本方法，
        调用方（IndexManager.remove_documents）按 supports_remove 走
        load → delete → save 通用回退；直接消费方收到显式异常。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 不支持按 ID 删除文档（supports_remove=False），"
            "请走 IndexManager 的 load→delete→save 回退或重建索引"
        )

    def remove_documents_by_metadata(
        self,
        collection_name: str,
        key: str,
        value: str,
    ) -> int:
        """按元数据删除文档，返回删除数量

        默认实现抛 NotImplementedError：3.3 禁止"返回 0 假成功"——不支持
        元数据删除的后端（faiss）不再覆写本方法。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 不支持按元数据删除文档，"
            "请走 IndexManager 的 load→遍历 docstore→delete 回退"
        )

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
