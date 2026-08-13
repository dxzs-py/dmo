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
            cursor.execute(
                f"SELECT vector_dims(embedding) FROM {row[0]} LIMIT 1"
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

            # 注意：必须引用模块级 _classes（值拷贝导入会拿到初始化时的 None，
            # 导致幂等判断恒为假、每次调用都重新进入 _get_embedding_collection_store）
            if _lcpg_vs._classes is not None:
                return
            dim = _current_db_dimension()
            if dim is None:
                logger.warning("PGVector 预热跳过：无法解析 embedding 列维度")
                return
            _lcpg_vs._get_embedding_collection_store(dim)
            logger.info(f"PGVector 映射类预热完成 (dimension={dim})")
        except Exception as e:
            logger.warning(f"PGVector 预热失败(非致命): {e}")


def reset_pgvector_cache(dimension=None):
    """维度调整后统一重置：clear metadata + 重置 _classes + 重新预热。

    由 ensure_embedding_dimension / kb_service 维度重试路径调用，
    修复旧代码"只 clear metadata 不清 _classes"的缓存不一致。
    """
    with _lock:
        try:
            import langchain_postgres.vectorstores as _lcpg_vs

            _lcpg_vs.Base.metadata.clear()
            _lcpg_vs._classes = None
            dim = dimension or _current_db_dimension()
            if dim is not None:
                _lcpg_vs._get_embedding_collection_store(dim)
            logger.info(f"PGVector 缓存已重置 (dimension={dim})")
        except Exception as e:
            logger.warning(f"PGVector 缓存重置失败(非致命): {e}")
