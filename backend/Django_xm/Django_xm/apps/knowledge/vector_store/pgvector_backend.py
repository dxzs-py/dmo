"""PGVector 向量存储后端实现

使用 PostgreSQL + pgvector 扩展存储和检索向量数据。
通过 Django ORM 操作数据库，避免硬编码表名。
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from django.db import connections

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from .base import VectorStoreBackend

logger = logging.getLogger(__name__)


def _get_pgvector_connection_string(async_mode: bool = False) -> str:
    """构建 PostgreSQL 连接字符串

    Args:
        async_mode: 是否使用异步驱动（asyncpg）
    """
    from django.conf import settings

    db = settings.DATABASES["default"]
    driver = "+asyncpg" if async_mode else ""
    return (
        f"postgresql{driver}://{db['USER']}:{db['PASSWORD']}"
        f"@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )


def _get_collection_table_name() -> str:
    """动态获取 PGVector collection 表名"""
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'langchain_pg_collection' "
            "LIMIT 1"
        )
        row = cursor.fetchone()
        return row[0] if row else "langchain_pg_collection"


def _get_embedding_table_name() -> str:
    """动态获取 PGVector embedding 表名"""
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'langchain_pg_embedding' "
            "LIMIT 1"
        )
        row = cursor.fetchone()
        return row[0] if row else "langchain_pg_embedding"


def _check_table_exists(table_name: str) -> bool:
    """检查表是否存在"""
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            [table_name],
        )
        return cursor.fetchone()[0]


class PGVectorBackend(VectorStoreBackend):
    """PGVector 向量存储后端"""

    def __init__(self, connection_string: Optional[str] = None):
        self._connection_string = connection_string

    @property
    def connection_string(self) -> str:
        if self._connection_string is None:
            self._connection_string = _get_pgvector_connection_string(async_mode=False)
        return self._connection_string

    def _get_pgvector_class(self):
        """延迟导入 PGVector 类"""
        try:
            from langchain_postgres.vectorstores import PGVector as LangChainPGVector
            return LangChainPGVector
        except ImportError:
            raise ImportError(
                "PGVector 未安装。请运行: pip install langchain-postgres"
            )

    @property
    def store_type(self) -> str:
        return "pgvector"

    def create(
        self,
        documents: List[Document],
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        LangChainPGVector = self._get_pgvector_class()

        vector_store = LangChainPGVector.from_documents(
            documents=documents,
            embedding=embeddings,
            collection_name=collection_name,
            connection=self.connection_string,
            use_jsonb=True,
            create_extension=False,
            **kwargs,
        )
        logger.info(f"PGVector 向量库创建成功 (collection={collection_name})")
        return vector_store

    def load(
        self,
        embeddings: Embeddings,
        collection_name: str,
        **kwargs: Any,
    ) -> VectorStore:
        LangChainPGVector = self._get_pgvector_class()

        vector_store = LangChainPGVector(
            embeddings=embeddings,
            collection_name=collection_name,
            connection=self.connection_string,
            use_jsonb=True,
            create_extension=False,
            **kwargs,
        )
        logger.info(f"PGVector 向量库加载成功 (collection={collection_name})")
        return vector_store

    def save(
        self,
        vector_store: VectorStore,
        path: str,
        **kwargs: Any,
    ) -> None:
        """PGVector 自动持久化，此方法为空操作"""
        logger.debug("PGVector 自动持久化到 PostgreSQL，无需手动保存")

    def delete(self, collection_name: str) -> bool:
        collection_table = _get_collection_table_name()
        if not _check_table_exists(collection_table):
            logger.warning(f"PGVector 集合表不存在，无需删除: {collection_name}")
            return False

        with connections["default"].cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {collection_table} WHERE name = %s",
                [collection_name],
            )
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"PGVector 集合已删除: {collection_name}")
            else:
                logger.warning(f"PGVector 集合不存在: {collection_name}")
            return deleted > 0

    def list_collections(self, prefix: str = "") -> List[str]:
        collection_table = _get_collection_table_name()
        if not _check_table_exists(collection_table):
            return []

        with connections["default"].cursor() as cursor:
            if prefix:
                cursor.execute(
                    f"SELECT name FROM {collection_table} WHERE name LIKE %s",
                    [f"{prefix}%"],
                )
            else:
                cursor.execute(f"SELECT name FROM {collection_table}")
            return [row[0] for row in cursor.fetchall()]

    def exists(self, collection_name: str) -> bool:
        collection_table = _get_collection_table_name()
        if not _check_table_exists(collection_table):
            return False

        with connections["default"].cursor() as cursor:
            cursor.execute(
                f"SELECT EXISTS(SELECT 1 FROM {collection_table} WHERE name = %s)",
                [collection_name],
            )
            return cursor.fetchone()[0]

    def add_documents(
        self,
        vector_store: VectorStore,
        documents: List[Document],
    ) -> List[str]:
        if hasattr(vector_store, "add_documents"):
            ids = vector_store.add_documents(documents)
        elif hasattr(vector_store, "add_texts"):
            texts = [doc.page_content for doc in documents]
            metadatas = [doc.metadata for doc in documents]
            ids = vector_store.add_texts(texts, metadatas)
        else:
            raise ValueError("PGVector 向量库不支持添加文档")
        logger.info(f"PGVector 添加 {len(ids)} 个文档")
        return ids

    def remove_documents(
        self,
        collection_name: str,
        document_ids: List[str],
    ) -> bool:
        if not document_ids:
            return True

        embedding_table = _get_embedding_table_name()
        if not _check_table_exists(embedding_table):
            logger.warning("PGVector embedding 表不存在")
            return False

        collection_table = _get_collection_table_name()
        with connections["default"].cursor() as cursor:
            # 通过 collection_name 找到 collection_id，再删除对应文档
            cursor.execute(
                f"SELECT uuid FROM {collection_table} WHERE name = %s",
                [collection_name],
            )
            row = cursor.fetchone()
            if not row:
                logger.warning(f"PGVector 集合不存在: {collection_name}")
                return False

            collection_id = row[0]
            placeholders = ", ".join(["%s"] * len(document_ids))
            cursor.execute(
                f"DELETE FROM {embedding_table} "
                f"WHERE collection_id = %s AND id::text IN ({placeholders})",
                [collection_id] + document_ids,
            )
            deleted_count = cursor.rowcount
            logger.info(
                f"PGVector 从集合 {collection_name} 删除 {deleted_count} 个文档"
            )
            return deleted_count > 0

    def remove_documents_by_metadata(
        self,
        collection_name: str,
        key: str,
        value: str,
    ) -> int:
        embedding_table = _get_embedding_table_name()
        collection_table = _get_collection_table_name()

        if not _check_table_exists(embedding_table) or not _check_table_exists(
            collection_table
        ):
            logger.warning("PGVector 表不存在")
            return 0

        with connections["default"].cursor() as cursor:
            # 查找匹配的文档 ID
            cursor.execute(
                f"SELECT id FROM {embedding_table} "
                f"WHERE collection_id = "
                f"(SELECT uuid FROM {collection_table} WHERE name = %s) "
                f"AND cmetadata->>%s = %s",
                [collection_name, key, value],
            )
            ids_to_delete = [str(row[0]) for row in cursor.fetchall()]

            if not ids_to_delete:
                logger.info(
                    f"PGVector 集合 {collection_name} 中未找到 "
                    f"metadata[{key}]={value} 的文档"
                )
                return 0

            # 删除匹配的文档
            placeholders = ", ".join(["%s"] * len(ids_to_delete))
            cursor.execute(
                f"DELETE FROM {embedding_table} WHERE id::text IN ({placeholders})",
                ids_to_delete,
            )
            deleted_count = cursor.rowcount
            logger.info(
                f"PGVector 按元数据删除 {deleted_count} 个文档 "
                f"(collection={collection_name}, {key}={value})"
            )
            return deleted_count

    def read_all_documents(
        self,
        collection_name: str,
    ) -> List[Document]:
        """读取集合中所有文档"""
        embedding_table = _get_embedding_table_name()
        collection_table = _get_collection_table_name()

        if not _check_table_exists(embedding_table) or not _check_table_exists(
            collection_table
        ):
            return []

        with connections["default"].cursor() as cursor:
            cursor.execute(
                f"SELECT document, cmetadata FROM {embedding_table} "
                f"WHERE collection_id = "
                f"(SELECT uuid FROM {collection_table} WHERE name = %s)",
                [collection_name],
            )
            documents = []
            for content, metadata in cursor.fetchall():
                if content and metadata:
                    documents.append(
                        Document(page_content=content, metadata=metadata if isinstance(metadata, dict) else {})
                    )
            logger.info(
                f"PGVector 从集合 {collection_name} 读取 {len(documents)} 个文档"
            )
            return documents

    def remove_documents_by_metadata_like(
        self,
        collection_name: str,
        key: str,
        pattern: str,
    ) -> int:
        """按元数据 LIKE 模式删除文档，返回删除数量"""
        embedding_table = _get_embedding_table_name()
        collection_table = _get_collection_table_name()

        if not _check_table_exists(embedding_table) or not _check_table_exists(
            collection_table
        ):
            logger.warning("PGVector 表不存在")
            return 0

        with connections["default"].cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM {embedding_table} "
                f"WHERE collection_id = "
                f"(SELECT uuid FROM {collection_table} WHERE name = %s) "
                f"AND cmetadata->>%s LIKE %s",
                [collection_name, key, pattern],
            )
            ids_to_delete = [str(row[0]) for row in cursor.fetchall()]

            if not ids_to_delete:
                logger.info(
                    f"PGVector 集合 {collection_name} 中未找到 "
                    f"metadata[{key}] LIKE {pattern} 的文档"
                )
                return 0

            placeholders = ", ".join(["%s"] * len(ids_to_delete))
            cursor.execute(
                f"DELETE FROM {embedding_table} WHERE id::text IN ({placeholders})",
                ids_to_delete,
            )
            deleted_count = cursor.rowcount
            logger.info(
                f"PGVector 按元数据 LIKE 删除 {deleted_count} 个文档 "
                f"(collection={collection_name}, {key} LIKE {pattern})"
            )
            return deleted_count

    def search(
        self,
        vector_store: VectorStore,
        query: str,
        k: int = 4,
        filter: Optional[Dict] = None,
    ) -> List[Tuple[Document, float]]:
        kwargs: Dict[str, Any] = {"k": k}
        if filter:
            kwargs["filter"] = filter
        return vector_store.similarity_search_with_score(query=query, **kwargs)

    def get_stats(self, collection_name: str) -> Dict[str, Any]:
        collection_table = _get_collection_table_name()
        embedding_table = _get_embedding_table_name()

        if not _check_table_exists(collection_table):
            return {
                "name": collection_name,
                "exists": False,
                "store_type": "pgvector",
            }

        with connections["default"].cursor() as cursor:
            # 检查集合是否存在
            cursor.execute(
                f"SELECT uuid FROM {collection_table} WHERE name = %s",
                [collection_name],
            )
            row = cursor.fetchone()
            if not row:
                return {
                    "name": collection_name,
                    "exists": False,
                    "store_type": "pgvector",
                }

            collection_id = row[0]

            # 统计文档数量
            doc_count = 0
            if _check_table_exists(embedding_table):
                cursor.execute(
                    f"SELECT COUNT(*) FROM {embedding_table} WHERE collection_id = %s",
                    [collection_id],
                )
                doc_count = cursor.fetchone()[0]

            return {
                "name": collection_name,
                "exists": True,
                "store_type": "pgvector",
                "num_documents": doc_count,
            }
