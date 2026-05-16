"""
Checkpointer 工厂模块

提供统一的 Checkpointer 创建接口，支持多种持久化后端：
1. PostgreSQL（生产推荐）- 支持多进程共享、高可用
2. SQLite（开发/单进程）- 轻量级，无需外部数据库
3. Memory（测试）- 内存存储，重启丢失

参考：
- https://langchain-ai.github.io/langgraph/concepts/persistence/
- https://langchain-ai.github.io/langgraph/reference/checkpoints/
"""

import os
from collections import OrderedDict
from typing import Optional, Any

from Django_xm.apps.ai_engine.config import settings, get_logger

logger = get_logger(__name__)

_checkpointer_cache: OrderedDict = OrderedDict()
_store_cache: OrderedDict = OrderedDict()
_CACHE_MAXSIZE = 16


def get_checkpointer(
    backend: Optional[str] = None,
    db_path: Optional[str] = None,
    connection_string: Optional[str] = None,
) -> Any:
    backend = backend or getattr(settings, "checkpointer_backend", "sqlite")
    cache_key = f"{backend}:{db_path or ''}:{connection_string or ''}"

    if cache_key in _checkpointer_cache:
        _checkpointer_cache.move_to_end(cache_key)
        return _checkpointer_cache[cache_key]

    if backend == "postgres":
        checkpointer = _create_postgres_checkpointer(connection_string)
    elif backend == "sqlite":
        checkpointer = _create_sqlite_checkpointer(db_path)
    elif backend == "memory":
        checkpointer = _create_memory_checkpointer()
    else:
        logger.warning(f"未知的 checkpointer 后端: {backend}，回退到 memory")
        checkpointer = _create_memory_checkpointer()

    _checkpointer_cache[cache_key] = checkpointer
    _checkpointer_cache.move_to_end(cache_key)
    if len(_checkpointer_cache) > _CACHE_MAXSIZE:
        evicted_key, _ = _checkpointer_cache.popitem(last=False)
        logger.debug(f"Checkpointer 缓存已满，LRU淘汰: {evicted_key}")
    return checkpointer


def _build_connection_string() -> str:
    """
    构建 PostgreSQL 连接字符串

    优先级：环境变量 CHECKPOINTER_POSTGRES_URI > settings 配置 > 拼接

    安全措施：
    - 优先从环境变量读取完整连接字符串，避免密码出现在代码中
    - 日志中始终遮蔽密码
    """
    connection_string = os.environ.get("CHECKPOINTER_POSTGRES_URI")
    if connection_string:
        return connection_string

    connection_string = getattr(settings, "checkpointer_postgres_uri", None)
    if connection_string:
        return connection_string

    db_host = os.environ.get("DB_HOST", getattr(settings, "db_host", "127.0.0.1"))
    db_port = os.environ.get("DB_PORT", getattr(settings, "db_port", 5432))
    db_name = os.environ.get("DB_NAME", getattr(settings, "db_name", "langchain_xm"))
    db_user = os.environ.get("DB_USER", "postgres")
    db_pass = os.environ.get("DB_PASSWORD", "")

    return f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"


def _create_postgres_checkpointer(connection_string: Optional[str] = None) -> Any:
    """
    创建 PostgreSQL 持久化 Checkpointer

    使用 langgraph-checkpoint-postgres 的 PostgresSaver，
    适合生产环境，支持多进程共享状态。

    Args:
        connection_string: PostgreSQL 连接字符串，
            格式: postgresql://user:pass@host:port/dbname
            默认从环境变量或 settings 读取。
    """
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError:
        logger.warning(
            "langgraph-checkpoint-postgres 未安装，回退到 SQLite。"
            "安装命令: pip install langgraph-checkpoint-postgres"
        )
        return _create_sqlite_checkpointer()

    if connection_string is None:
        connection_string = _build_connection_string()

    logger.info(f"创建 PostgreSQL Checkpointer: {_mask_connection_string(connection_string)}")

    try:
        checkpointer = PostgresSaver.from_conn_string(connection_string)
        if hasattr(checkpointer, '__enter__'):
            checkpointer = checkpointer.__enter__()
        if hasattr(checkpointer, 'setup'):
            checkpointer.setup()
        logger.info("PostgreSQL Checkpointer 创建成功")
        return checkpointer
    except Exception as e:
        logger.error(f"创建 PostgreSQL Checkpointer 失败: {e}，回退到 SQLite")
        return _create_sqlite_checkpointer()


async def _create_async_sqlite_checkpointer(db_path: Optional[str] = None) -> Any:
    """创建异步 SQLite Checkpointer"""
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError:
        logger.warning(
            "langgraph-checkpoint-sqlite 未安装，无法创建异步 SQLite Checkpointer。"
            "安装命令: pip install langgraph-checkpoint-sqlite"
        )
        return None

    try:
        import aiosqlite
    except ImportError:
        logger.warning(
            "aiosqlite 未安装，无法创建异步 SQLite Checkpointer。"
            "安装命令: pip install aiosqlite"
        )
        return None

    if db_path is None:
        data_dir = getattr(settings, "data_dir", None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "data"
        )
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "checkpoints.db")

    logger.info(f"创建异步 SQLite Checkpointer: {db_path}")

    try:
        conn = await aiosqlite.connect(db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        checkpointer = AsyncSqliteSaver(conn)
        if hasattr(checkpointer, 'setup'):
            await checkpointer.setup()
        logger.info("异步 SQLite Checkpointer 创建成功")
        return checkpointer
    except Exception as e:
        logger.error(f"创建异步 SQLite Checkpointer 失败: {e}")
        return None


def _create_async_postgres_checkpointer(connection_string: Optional[str] = None) -> Any:
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError:
        logger.warning(
            "langgraph-checkpoint-postgres 未安装，无法创建异步 Checkpointer。"
            "安装命令: pip install langgraph-checkpoint-postgres"
        )
        return None

    if connection_string is None:
        connection_string = _build_connection_string()

    logger.info(f"创建异步 PostgreSQL Checkpointer: {_mask_connection_string(connection_string)}")

    try:
        checkpointer = AsyncPostgresSaver.from_conn_string(connection_string)
        logger.info("异步 PostgreSQL Checkpointer 创建成功（需 await setup()）")
        return checkpointer
    except Exception as e:
        logger.error(f"创建异步 PostgreSQL Checkpointer 失败: {e}")
        return None


async def get_async_checkpointer(
    backend: Optional[str] = None,
    connection_string: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Any:
    backend = backend or getattr(settings, "checkpointer_backend", "sqlite")
    cache_key = f"async:{backend}:{db_path or ''}:{connection_string or ''}"

    if cache_key in _checkpointer_cache:
        return _checkpointer_cache[cache_key]

    if backend == "postgres":
        checkpointer = _create_async_postgres_checkpointer(connection_string)
        if checkpointer is not None:
            if hasattr(checkpointer, "setup"):
                await checkpointer.setup()
            _checkpointer_cache[cache_key] = checkpointer
            return checkpointer
        logger.warning("异步 PostgreSQL Checkpointer 创建失败，回退到异步 SQLite")
        return await get_async_checkpointer(backend="sqlite", db_path=db_path)
    elif backend == "sqlite":
        checkpointer = await _create_async_sqlite_checkpointer(db_path)
        if checkpointer is not None:
            _checkpointer_cache[cache_key] = checkpointer
            return checkpointer
        logger.warning("异步 SQLite Checkpointer 创建失败，回退到同步 SQLite")
        return get_checkpointer(backend="sqlite", db_path=db_path)
    else:
        return get_checkpointer(backend=backend)


def _create_sqlite_checkpointer(db_path: Optional[str] = None) -> Any:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        if db_path is None:
            data_dir = getattr(settings, "data_dir", None) or os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                "data"
            )
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, "checkpoints.db")

        logger.info(f"创建 SQLite Checkpointer: {db_path}")

        try:
            import sqlite3
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            checkpointer = SqliteSaver(conn)
            if hasattr(checkpointer, 'setup'):
                checkpointer.setup()
            logger.info("SQLite Checkpointer 创建成功（独立连接）")
            return checkpointer
        except TypeError:
            pass

        checkpointer = SqliteSaver.from_conn_string(db_path)

        if hasattr(checkpointer, 'setup'):
            checkpointer.setup()
            logger.info("SQLite Checkpointer 创建成功（直接 setup）")
            return checkpointer

        if hasattr(checkpointer, '__enter__'):
            checkpointer = checkpointer.__enter__()
            if hasattr(checkpointer, 'setup'):
                checkpointer.setup()
            logger.info("SQLite Checkpointer 创建成功（上下文管理器模式）")
            return checkpointer

        logger.info("SQLite Checkpointer 创建成功（无需 setup）")
        return checkpointer

    except ImportError:
        logger.warning(
            "langgraph-checkpoint-sqlite 未安装，回退到 MemorySaver。"
            "安装命令: pip install langgraph-checkpoint-sqlite"
        )
        return _create_memory_checkpointer()
    except Exception as e:
        logger.error(f"创建 SQLite Checkpointer 失败: {e}，回退到 MemorySaver")
        return _create_memory_checkpointer()


def _create_memory_checkpointer() -> Any:
    """
    创建内存 Checkpointer（仅用于开发/测试）

    警告：服务重启后状态会丢失，生产环境请使用 PostgresSaver。
    """
    from langgraph.checkpoint.memory import MemorySaver

    logger.warning("使用 MemorySaver（内存存储），服务重启后状态将丢失")
    return MemorySaver()


def _mask_connection_string(conn_str: str) -> str:
    """隐藏连接字符串中的密码"""
    if "://" in conn_str and "@" in conn_str:
        try:
            prefix = conn_str.split("://")[0] + "://"
            rest = conn_str.split("://")[1]
            if ":" in rest.split("@")[0]:
                user = rest.split(":")[0]
                after_at = rest.split("@")[1]
                return f"{prefix}{user}:****@{after_at}"
        except (IndexError, ValueError):
            pass
    return "***"


def get_store(backend: Optional[str] = None) -> Any:
    backend = backend or getattr(settings, "store_backend", "memory")
    cache_key = f"store:{backend}"

    if cache_key in _store_cache:
        return _store_cache[cache_key]

    if backend == "postgres":
        store = _create_postgres_store()
    elif backend == "memory":
        store = _create_memory_store()
    else:
        logger.warning(f"未知的 store 后端: {backend}，回退到 memory")
        store = _create_memory_store()

    if store is not None:
        _store_cache[cache_key] = store
    return store


def _create_memory_store() -> Any:
    """
    创建内存 Store（开发/测试）

    使用 langgraph.store.memory.InMemoryStore，
    跨线程共享长期记忆，但服务重启后丢失。
    """
    try:
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()
        logger.info("InMemoryStore 创建成功")
        return store
    except ImportError:
        logger.warning(
            "langgraph.store.memory.InMemoryStore 不可用，"
            "请升级 langgraph>=0.2.0"
        )
        return None


def _create_postgres_store() -> Any:
    """
    创建 PostgreSQL 持久化 Store（生产推荐）

    使用 langgraph-store-postgres 的 PostgresStore，
    支持跨进程、跨重启的长期记忆持久化。
    """
    try:
        from langgraph.store.postgres import PostgresStore
    except ImportError:
        logger.warning(
            "langgraph-store-postgres 未安装，回退到 InMemoryStore。"
            "安装命令: pip install langgraph-store-postgres"
        )
        return _create_memory_store()

    connection_string = _build_connection_string()

    logger.info(f"创建 PostgreSQL Store: {_mask_connection_string(connection_string)}")

    try:
        store = PostgresStore.from_conn_string(connection_string)
        store.setup()
        logger.info("PostgreSQL Store 创建成功")
        return store
    except Exception as e:
        logger.error(f"创建 PostgreSQL Store 失败: {e}，回退到 InMemoryStore")
        return _create_memory_store()


def ensure_store(instance) -> Any:
    if getattr(instance, '_store', None) is not None:
        return instance._store
    try:
        instance._store = get_store()
        return instance._store
    except Exception as e:
        logger.warning(f"Store 不可用: {e}")
        return None
