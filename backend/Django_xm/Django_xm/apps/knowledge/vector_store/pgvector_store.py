"""PGVector 向量存储实现

使用 PostgreSQL + pgvector 扩展存储和检索向量数据。
"""
import logging
from typing import List, Optional, Dict, Any

from django.conf import settings

logger = logging.getLogger(__name__)


def get_pgvector_connection_string(async_mode: bool = False):
    """构建 PostgreSQL 连接字符串

    Args:
        async_mode: 是否使用异步驱动（asyncpg）
    """
    db_settings = settings.DATABASES['default']
    driver = "+asyncpg" if async_mode else ""
    return (
        f"postgresql{driver}://{db_settings['USER']}:{db_settings['PASSWORD']}"
        f"@{db_settings['HOST']}:{db_settings['PORT']}/{db_settings['NAME']}"
    )


def create_pgvector_store(
    collection_name: str,
    embedding=None,
    pre_collection_name: str = "",
    user_id: Optional[int] = None,
):
    """创建 PGVector 向量存储

    Args:
        collection_name: 集合名称（知识库名）
        embedding: Embedding 模型实例
        pre_collection_name: 前缀集合名（含 user_id 前缀）
        user_id: 用户 ID
    """
    try:
        from langchain_postgres.vectorstores import PGVector as LangChainPGVector
    except ImportError:
        logger.error("langchain-postgres 未安装，请运行: pip install langchain-postgres")
        raise

    if embedding is None:
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
        embedding = get_embeddings()

    # 使用带用户前缀的集合名
    table_name = pre_collection_name if pre_collection_name else collection_name

    connection_string = get_pgvector_connection_string(async_mode=False)

    vectorstore = LangChainPGVector(
        embeddings=embedding,
        collection_name=table_name,
        connection=connection_string,
        use_jsonb=True,
        create_extension=False,
    )

    logger.info(f"PGVector 向量存储已创建: collection={table_name}")
    return vectorstore


def load_pgvector_store(
    collection_name: str,
    embedding=None,
    pre_collection_name: str = "",
):
    """加载已有的 PGVector 向量存储"""
    try:
        from langchain_postgres.vectorstores import PGVector as LangChainPGVector
    except ImportError:
        raise ImportError("langchain-postgres 未安装")

    if embedding is None:
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
        embedding = get_embeddings()

    table_name = pre_collection_name if pre_collection_name else collection_name
    connection_string = get_pgvector_connection_string(async_mode=False)

    vectorstore = LangChainPGVector(
        embeddings=embedding,
        collection_name=table_name,
        connection=connection_string,
        use_jsonb=True,
        create_extension=False,
    )

    logger.info(f"PGVector 向量存储已加载: collection={table_name}")
    return vectorstore


def delete_pgvector_store(
    collection_name: str,
    pre_collection_name: str = "",
):
    """删除 PGVector 向量存储（删除集合中的所有数据）"""
    try:
        from django.db import connections

        table_name = pre_collection_name if pre_collection_name else collection_name

        with connections['default'].cursor() as cursor:
            # 先检查表是否存在
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'langchain_pg_collection')"
            )
            if not cursor.fetchone()[0]:
                logger.warning(f"PGVector 集合表不存在，无需删除: {table_name}")
                return False

            # 删除 langchain_pg_collection 中的记录（会级联删除向量数据）
            cursor.execute(
                "DELETE FROM langchain_pg_collection WHERE name = %s",
                [table_name]
            )
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"PGVector 集合已删除: {table_name}")
            else:
                logger.warning(f"PGVector 集合不存在: {table_name}")
            return deleted > 0
    except Exception as e:
        logger.error(f"删除 PGVector 集合失败: {e}")
        return False


def list_pgvector_stores(user_id: Optional[int] = None):
    """列出所有 PGVector 集合"""
    try:
        from django.db import connections

        with connections['default'].cursor() as cursor:
            # 先检查表是否存在（PGVector 表在首次使用时才创建）
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'langchain_pg_collection')"
            )
            if not cursor.fetchone()[0]:
                return []

            if user_id:
                cursor.execute(
                    "SELECT name FROM langchain_pg_collection WHERE name LIKE %s",
                    [f"user_{user_id}_%"]
                )
            else:
                cursor.execute("SELECT name FROM langchain_pg_collection")
            return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.debug(f"列出 PGVector 集合: {e}")
        return []
