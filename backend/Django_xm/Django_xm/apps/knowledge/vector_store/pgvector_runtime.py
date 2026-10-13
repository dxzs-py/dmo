"""PGVector 运行时管理：启动预热 + 缓存重置（线程安全）。

背景：
- langchain_postgres._get_embedding_collection_store() 懒加载定义
  CollectionStore/EmbeddingStore 映射类，_classes 全局缓存"检查-赋值"非原子，
  celery -P threads 线程池并发首次初始化时重复定义同名 Table →
  InvalidRequestError: Table 'langchain_pg_collection' is already defined。
- 旧代码 Base.metadata.clear() 只清 MetaData 表定义、不清 _classes 缓存，
  维度调整后仍返回旧类（旧维度列类型），清缓存无效。

职责：
- warm_up_pgvector_runtime()：进程启动单线程预热，填充 _classes 缓存
  （消除并发首次初始化竞态窗口）。
- reset_pgvector_cache()：维度调整后统一重置（clear metadata + 重置 _classes
  + 按新维度重新预热），供 ensure_embedding_dimension / kb_service 维度重试调用。
"""

import logging
import threading

from Django_xm.common.exceptions import DimensionMismatchError

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _current_db_dimension():
    """读取 DB 当前 embedding 列维度；无表/读取失败返回 None。"""
    try:
        from django.db import connections

        with connections["default"].cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE 'langchain_pg_embedding' LIMIT 1"
            )
            row = cursor.fetchone()
            if not row:
                return None
            # row[0] 来自 information_schema.tables，WHERE table_name LIKE 'langchain_pg_embedding'
            # 无通配符即精确匹配，属固定白名单常量（非用户输入）；PostgreSQL 表名无法参数化
            cursor.execute(
                f"SELECT vector_dims(embedding) FROM {row[0]} LIMIT 1"  # noqa: S608
            )
            r2 = cursor.fetchone()
            return r2[0] if r2 else None
    except Exception:
        return None


def warm_up_pgvector_runtime():
    """进程启动单线程预热（幂等）。无维度信息时跳过（由首次真实使用定义）。"""
    with _lock:
        try:
            import langchain_postgres.vectorstores as _lcpg_vs

            if _lcpg_vs._classes is not None:
                """
                - `_classes = None`：**还没有根据维度生成 ORM 模型**
                - `_classes != None`：**已经生成过一次模型，缓存好了，不用再生成**
                """
                return
            dim = _current_db_dimension()
            if dim is None:
                logger.warning("PGVector 预热跳过：无法解析 embedding 列维度")
                return
            # **核心预热动作**：传入维度，触发 langchain_postgres
            # 内部动态生成 EmbeddingStore / CollectionStore
            # 两个 SQLAlchemy ORM 模型，赋值给模块全局变量 `_classes`。
            _lcpg_vs._get_embedding_collection_store(dim)
            logger.info(f"PGVector 映射类预热完成 (dimension={dim})")
        except Exception as e:
            logger.warning(f"PGVector 预热失败(非致命): {e}")


def as_dimension_mismatch_error(exc: BaseException) -> DimensionMismatchError | None:
    """pgvector 维度不匹配识别收口：pgvector 专属知识不再内嵌通用检索入口

    DB 层（pgvector 扩展）对"查询向量维度 != 列维度"只以错误消息文本暴露
    （"different vector dimensions: X != Y"），无独立 SQLSTATE 可靠判定，
    消息子串是唯一信号——该识别原先内嵌在 rag_retrieval.search_scored 通用
    入口，现收口至 pgvector 层，命中即包装为类型化 DimensionMismatchError
    （isinstance 判定优先，对齐 CT-06 EmbeddingUnavailableError 先例；
    原始异常经 __cause__ 保留）。

    Returns:
        DimensionMismatchError（识别命中，含已类型化异常则原样返回）或 None
    """
    if isinstance(exc, DimensionMismatchError):
        return exc
    if "different vector dimensions" in str(exc):
        wrapped = DimensionMismatchError(str(exc))
        wrapped.__cause__ = exc
        return wrapped
    return None


def reset_pgvector_cache(dimension=None):
    """维度调整后统一重置：clear metadata + 重置 _classes + 重新预热。

    由 ensure_embedding_dimension / kb_service 维度重试路径调用，
    修复旧代码"只 clear metadata 不清 _classes"的缓存不一致。
    """
    with _lock:
        try:
            import langchain_postgres.vectorstores as _lcpg_vs

            _lcpg_vs.Base.metadata.clear()  # `.clear()`：**清空 SQLAlchemy 元数据表注册表**
            _lcpg_vs._classes = None  # 把模块全局缓存 `_classes` 手动置空。
            dim = dimension or _current_db_dimension()
            if dim is not None:
                _lcpg_vs._get_embedding_collection_store(dim)
            logger.info(f"PGVector 缓存已重置 (dimension={dim})")
        except Exception as e:
            logger.warning(f"PGVector 缓存重置失败(非致命): {e}")
