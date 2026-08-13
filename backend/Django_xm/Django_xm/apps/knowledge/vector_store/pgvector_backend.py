"""PGVector 向量存储后端实现

使用 PostgreSQL + pgvector 扩展存储和检索向量数据。
通过 Django ORM 操作数据库，避免硬编码表名。

所有原生 SQL 中的表名均通过 ``connections["default"].ops.quote_name()``
包装（Task 20.2），防止 SQL 注入并兼容大小写敏感的标识符。
"""

import logging
from typing import Any

from django.db import connections
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from .base import VectorStoreBackend

logger = logging.getLogger(__name__)


def _quote_identifier(name: str) -> str:
    """使用 Django ORM 的 quote_name 包装 SQL 标识符（表名/列名）。

    确保表名被正确引用（如 ``"langchain_pg_collection"``），
    防止 SQL 注入与保留字冲突（Task 20.2）。
    """
    return connections["default"].ops.quote_name(name)


def _get_pgvector_connection_string(async_mode: bool = False) -> str:
    """构建 PostgreSQL 连接字符串

    Args:
        async_mode: 是否使用异步驱动（asyncpg）
    """
    from django.conf import settings

    db = settings.DATABASES["default"]
    driver = "+asyncpg" if async_mode else ""
    return f"postgresql{driver}://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"


def _get_collection_table_name() -> str:
    """动态获取 PGVector collection 表名"""
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'langchain_pg_collection' LIMIT 1"
        )
        row = cursor.fetchone()
        return row[0] if row else "langchain_pg_collection"


def _get_embedding_table_name() -> str:
    """动态获取 PGVector embedding 表名"""
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'langchain_pg_embedding' LIMIT 1"
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

    def __init__(self, connection_string: str | None = None):
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

            # 懒预热：首次使用前单线程填充 _classes 缓存，消除线程池并发
            # 首次初始化竞态（InvalidRequestError: already defined）。
            # 幂等 + threading.Lock 保护，多线程并发调用仅一次真正预热。
            from Django_xm.apps.knowledge.vector_store.pgvector_runtime import (
                warm_up_pgvector_runtime,
            )

            warm_up_pgvector_runtime()
            return LangChainPGVector
        except ImportError:
            raise ImportError("PGVector 未安装。请运行: pip install langchain-postgres") from None

    @property
    def store_type(self) -> str:
        return "pgvector"

    def get_embedding_column_dimension(self) -> int | None:
        """获取 PGVector embedding 列的当前向量维度"""
        embedding_table = _get_embedding_table_name()
        if not _check_table_exists(embedding_table):
            return None
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute(f"SELECT vector_dims(embedding) FROM {_quote_identifier(embedding_table)} LIMIT 1")  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def ensure_embedding_dimension(self, new_dimension: int) -> bool:
        """确保 PGVector embedding 列维度与新向量兼容

        当 embedding 维度变化时（如从 1024 切换到 1536），
        需要先清空旧数据并修改列类型。

        注意：embedding 列维度是表级的，所有集合共享。
        调用方应确保在重建所有索引时协调调用，先删除所有旧集合数据，
        再调用此方法调整列类型，最后逐个重建。

        Returns:
            True 表示维度已兼容（无需调整或调整成功），
            False 表示维度不兼容且无法调整（表中仍有其他集合数据）
        """
        embedding_table = _get_embedding_table_name()
        if not _check_table_exists(embedding_table):
            return True

        try:
            with connections["default"].cursor() as cursor:
                # 检查当前列维度
                cursor.execute(f"SELECT vector_dims(embedding) FROM {_quote_identifier(embedding_table)} LIMIT 1")  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                row = cursor.fetchone()
                if row is None:
                    # 表为空，直接 ALTER 列类型
                    cursor.execute(
                        f"ALTER TABLE {_quote_identifier(embedding_table)} "
                        f"ALTER COLUMN embedding TYPE vector({new_dimension})"
                    )
                    logger.info(f"PGVector embedding 列维度已调整为 vector({new_dimension})")
                    return True

                current_dim = row[0]
                if current_dim == new_dimension:
                    return True

                # 维度不匹配，检查表中是否还有数据
                cursor.execute(f"SELECT COUNT(*) FROM {_quote_identifier(embedding_table)}")  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                remaining = cursor.fetchone()[0]
                if remaining > 0:
                    logger.warning(
                        f"PGVector embedding 列维度 {current_dim} 与新维度 {new_dimension} 不匹配，"
                        f"但表中仍有 {remaining} 条数据，无法调整列类型。"
                        f"请先删除所有旧集合数据后再重建。"
                    )
                    return False

                # 表已空，可以安全 ALTER
                cursor.execute(
                    f"ALTER TABLE {_quote_identifier(embedding_table)} "
                    f"ALTER COLUMN embedding TYPE vector({new_dimension})"
                )
                logger.info(f"PGVector embedding 列维度已从 vector({current_dim}) 调整为 vector({new_dimension})")
                # ALTER 后关闭所有数据库连接，让 SQLAlchemy 重新建立连接
                # 否则已有连接仍缓存旧的列定义，导致维度不匹配错误
                connections.close_all()
                # 清理 IndexManager 的 VectorStore 缓存，并释放所有旧 engine 连接池
                from Django_xm.apps.knowledge.services.index_service import IndexManager

                for cached_vs in IndexManager._cache.values():
                    if hasattr(cached_vs, "_engine") and cached_vs._engine:
                        try:
                            cached_vs._engine.dispose()
                        except Exception:  # noqa: S110  # cleanup, 单个 engine 释放失败不影响其他
                            pass
                IndexManager._cache.clear()
                # 统一重置 PGVector 缓存（clear metadata + 重置 _classes + 按新维度重建）
                # 修复旧代码"只 clear metadata 不清 _classes"导致维度调整后仍返回旧类的缓存不一致
                from Django_xm.apps.knowledge.vector_store.pgvector_runtime import (
                    reset_pgvector_cache,
                )

                reset_pgvector_cache(dimension=new_dimension)
                logger.info("PGVector 维度调整后已关闭所有数据库连接、释放旧 engine 并清理缓存")
                return True
        except Exception as e:
            logger.warning(f"PGVector 维度调整失败: {e}")
            return False

    def create(
        self,
        documents: list[Document],
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
        embedding_table = _get_embedding_table_name()
        if not _check_table_exists(collection_table):
            logger.warning(f"PGVector 集合表不存在，无需删除: {collection_name}")
            return False

        with connections["default"].cursor() as cursor:
            # 先删除 embedding 表中关联的向量数据
            cursor.execute(
                f"DELETE FROM {_quote_identifier(embedding_table)} WHERE collection_id = "  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                f"(SELECT uuid FROM {_quote_identifier(collection_table)} WHERE name = %s)",
                [collection_name],
            )
            emb_deleted = cursor.rowcount
            if emb_deleted > 0:
                logger.info(f"PGVector 向量数据已删除: {emb_deleted} 条 (collection={collection_name})")

            # 再删除 collection 记录
            cursor.execute(
                f"DELETE FROM {_quote_identifier(collection_table)} WHERE name = %s",  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                [collection_name],
            )
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"PGVector 集合已删除: {collection_name}")
            else:
                logger.warning(f"PGVector 集合不存在: {collection_name}")
            return deleted > 0

    def list_collections(self, prefix: str = "") -> list[str]:
        collection_table = _get_collection_table_name()
        if not _check_table_exists(collection_table):
            return []

        with connections["default"].cursor() as cursor:
            if prefix:
                cursor.execute(
                    f"SELECT name FROM {_quote_identifier(collection_table)} WHERE name LIKE %s",  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                    [f"{prefix}%"],
                )
            else:
                cursor.execute(f"SELECT name FROM {_quote_identifier(collection_table)}")  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
            return [row[0] for row in cursor.fetchall()]

    def exists(self, collection_name: str) -> bool:
        collection_table = _get_collection_table_name()
        if not _check_table_exists(collection_table):
            return False

        with connections["default"].cursor() as cursor:
            cursor.execute(
                f"SELECT EXISTS(SELECT 1 FROM {_quote_identifier(collection_table)} WHERE name = %s)",  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                [collection_name],
            )
            return cursor.fetchone()[0]

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
            raise ValueError("PGVector 向量库不支持添加文档")
        logger.info(f"PGVector 添加 {len(ids)} 个文档")
        return ids

    def remove_documents(
        self,
        collection_name: str,
        document_ids: list[str],
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
                f"SELECT uuid FROM {_quote_identifier(collection_table)} WHERE name = %s",  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                [collection_name],
            )
            row = cursor.fetchone()
            if not row:
                logger.warning(f"PGVector 集合不存在: {collection_name}")
                return False

            collection_id = row[0]
            placeholders = ", ".join(["%s"] * len(document_ids))
            cursor.execute(
                f"DELETE FROM {_quote_identifier(embedding_table)} "  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                f"WHERE collection_id = %s AND id::text IN ({placeholders})",
                [collection_id, *document_ids],
            )
            deleted_count = cursor.rowcount
            logger.info(f"PGVector 从集合 {collection_name} 删除 {deleted_count} 个文档")
            return deleted_count > 0

    def remove_documents_by_metadata(
        self,
        collection_name: str,
        key: str,
        value: str,
    ) -> int:
        embedding_table = _get_embedding_table_name()
        collection_table = _get_collection_table_name()

        if not _check_table_exists(embedding_table) or not _check_table_exists(collection_table):
            logger.warning("PGVector 表不存在")
            return 0

        with connections["default"].cursor() as cursor:
            # 查找匹配的文档 ID
            cursor.execute(
                f"SELECT id FROM {_quote_identifier(embedding_table)} "  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                f"WHERE collection_id = "
                f"(SELECT uuid FROM {_quote_identifier(collection_table)} WHERE name = %s) "
                f"AND cmetadata->>%s = %s",
                [collection_name, key, value],
            )
            ids_to_delete = [str(row[0]) for row in cursor.fetchall()]

            if not ids_to_delete:
                logger.info(f"PGVector 集合 {collection_name} 中未找到 metadata[{key}]={value} 的文档")
                return 0

            # 删除匹配的文档
            placeholders = ", ".join(["%s"] * len(ids_to_delete))
            cursor.execute(
                f"DELETE FROM {_quote_identifier(embedding_table)} WHERE id::text IN ({placeholders})",  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                ids_to_delete,
            )
            deleted_count = cursor.rowcount
            logger.info(f"PGVector 按元数据删除 {deleted_count} 个文档 (collection={collection_name}, {key}={value})")
            return deleted_count

    def read_all_documents(
        self,
        collection_name: str,
    ) -> list[Document]:
        """读取集合中所有文档"""
        embedding_table = _get_embedding_table_name()
        collection_table = _get_collection_table_name()

        if not _check_table_exists(embedding_table) or not _check_table_exists(collection_table):
            return []

        with connections["default"].cursor() as cursor:
            cursor.execute(
                f"SELECT document, cmetadata FROM {_quote_identifier(embedding_table)} "  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                f"WHERE collection_id = "
                f"(SELECT uuid FROM {_quote_identifier(collection_table)} WHERE name = %s)",
                [collection_name],
            )
            documents = []
            for content, metadata in cursor.fetchall():
                if content and metadata:
                    documents.append(
                        Document(page_content=content, metadata=metadata if isinstance(metadata, dict) else {})
                    )
            logger.info(f"PGVector 从集合 {collection_name} 读取 {len(documents)} 个文档")
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

        if not _check_table_exists(embedding_table) or not _check_table_exists(collection_table):
            logger.warning("PGVector 表不存在")
            return 0

        with connections["default"].cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM {_quote_identifier(embedding_table)} "  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                f"WHERE collection_id = "
                f"(SELECT uuid FROM {_quote_identifier(collection_table)} WHERE name = %s) "
                f"AND cmetadata->>%s LIKE %s",
                [collection_name, key, pattern],
            )
            ids_to_delete = [str(row[0]) for row in cursor.fetchall()]

            if not ids_to_delete:
                logger.info(f"PGVector 集合 {collection_name} 中未找到 metadata[{key}] LIKE {pattern} 的文档")
                return 0

            placeholders = ", ".join(["%s"] * len(ids_to_delete))
            cursor.execute(
                f"DELETE FROM {_quote_identifier(embedding_table)} WHERE id::text IN ({placeholders})",  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
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
        filter: dict | None = None,
    ) -> list[tuple[Document, float]]:
        kwargs: dict[str, Any] = {"k": k}
        if filter:
            kwargs["filter"] = filter
        return vector_store.similarity_search_with_score(query=query, **kwargs)

    def get_stats(self, collection_name: str) -> dict[str, Any]:
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
                f"SELECT uuid FROM {_quote_identifier(collection_table)} WHERE name = %s",  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
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
                    f"SELECT COUNT(*) FROM {_quote_identifier(embedding_table)} WHERE collection_id = %s",  # noqa: S608  # parameterized query; table name quoted via _quote_identifier()
                    [collection_id],
                )
                doc_count = cursor.fetchone()[0]

            return {
                "name": collection_name,
                "exists": True,
                "store_type": "pgvector",
                "num_documents": doc_count,
            }
