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

import asyncio
import atexit
import os
import threading
from collections import OrderedDict
from typing import Any

from Django_xm.apps.ai_engine.config import settings
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

_checkpointer_cache: OrderedDict = OrderedDict()
_store_cache: OrderedDict = OrderedDict()
# 保存上下文管理器引用，防止连接池被 GC 回收关闭
_context_manager_refs: dict = {}
_CACHE_MAXSIZE = 64
_cache_lock = threading.Lock()


def _close_checkpointer(cache_key: str, checkpointer: Any) -> None:
    """安全关闭被 LRU 淘汰的 checkpointer，释放数据库连接"""
    try:
        # 尝试调用上下文管理器的 __exit__ 关闭连接
        # 同步实例使用 pg_sync: 前缀
        cm_ref = _context_manager_refs.pop(f"pg_sync:{id(checkpointer)}", None)
        if cm_ref is not None:
            cm_ref.__exit__(None, None, None)
            logger.debug(f"已关闭同步 checkpointer 上下文管理器: {cache_key}")
            return

        # 异步实例使用 pg_async: 前缀，需要异步关闭
        cm_ref = _context_manager_refs.pop(f"pg_async:{id(checkpointer)}", None)
        if cm_ref is not None:
            try:
                # 尝试在已有事件循环中关闭
                loop = asyncio.get_running_loop()
                loop.create_task(cm_ref.__aexit__(None, None, None))
            except RuntimeError:
                # 没有运行中的事件循环，创建新的来关闭
                try:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(cm_ref.__aexit__(None, None, None))
                    loop.close()
                except Exception as e:
                    logger.debug(f"异步关闭 checkpointer 失败 ({cache_key}): {e}")
            logger.debug(f"已关闭异步 checkpointer 上下文管理器: {cache_key}")
            return

        # 尝试直接关闭连接
        if hasattr(checkpointer, "close"):
            checkpointer.close()
            logger.debug(f"已关闭 checkpointer: {cache_key}")
    except Exception as e:
        logger.warning(f"关闭 checkpointer 失败 ({cache_key}): {e}")


def close_all_checkpointers() -> None:
    """关闭所有缓存的 checkpointer，释放连接资源（进程退出时调用）"""
    with _cache_lock:
        for key, val in _checkpointer_cache.items():
            _close_checkpointer(key, val)
        _checkpointer_cache.clear()

        for key, val in _store_cache.items():
            try:
                cm_ref = _context_manager_refs.pop(f"pg_store:{id(val)}", None)
                if cm_ref is not None:
                    cm_ref.__exit__(None, None, None)
                elif hasattr(val, "close"):
                    val.close()
            except Exception as e:
                logger.warning(f"关闭 store 失败 ({key}): {e}")
        _store_cache.clear()
        _context_manager_refs.clear()

    logger.info("所有 Checkpointer 和 Store 已关闭")


atexit.register(close_all_checkpointers)


def get_checkpointer(
    backend: str | None = None,
    db_path: str | None = None,
    connection_string: str | None = None,
) -> Any:
    backend = backend or getattr(settings, "checkpointer_backend", "sqlite")
    cache_key = f"{backend}:{db_path or ''}:{connection_string or ''}"

    with _cache_lock:
        if cache_key in _checkpointer_cache:
            _checkpointer_cache.move_to_end(cache_key)
            return _checkpointer_cache[cache_key]

        # 在锁内创建，防止并发 cache miss 导致重复创建和连接泄漏
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
            evicted_key, evicted_val = _checkpointer_cache.popitem(last=False)
            _close_checkpointer(evicted_key, evicted_val)
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


def _create_postgres_checkpointer(connection_string: str | None = None) -> Any:
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
            "langgraph-checkpoint-postgres 未安装，回退到 SQLite。安装命令: pip install langgraph-checkpoint-postgres"
        )
        return _create_sqlite_checkpointer()

    if connection_string is None:
        connection_string = _build_connection_string()

    logger.info(f"创建 PostgreSQL Checkpointer: {_mask_connection_string(connection_string)}")

    try:
        cm = PostgresSaver.from_conn_string(connection_string)
        # from_conn_string 返回上下文管理器，需要 __enter__ 获取实际实例
        if hasattr(cm, "__enter__"):
            checkpointer = cm.__enter__()
            # 保存上下文管理器引用，防止连接池被 GC 回收关闭
            _context_manager_refs[f"pg_sync:{id(checkpointer)}"] = cm
            atexit.register(cm.__exit__, None, None, None)
        else:
            checkpointer = cm
        if hasattr(checkpointer, "setup"):
            checkpointer.setup()
        logger.info("PostgreSQL Checkpointer 创建成功")
        return checkpointer
    except Exception:
        logger.exception("创建 PostgreSQL Checkpointer 失败，回退到 SQLite")
        return _create_sqlite_checkpointer()


async def _create_async_sqlite_checkpointer(db_path: str | None = None) -> Any:
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
        logger.warning("aiosqlite 未安装，无法创建异步 SQLite Checkpointer。安装命令: pip install aiosqlite")
        return None

    if db_path is None:
        data_dir = getattr(settings, "data_dir", None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data"
        )
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "checkpoints.db")

    logger.info(f"创建异步 SQLite Checkpointer: {db_path}")

    try:
        conn = await aiosqlite.connect(db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        checkpointer = AsyncSqliteSaver(conn)
        if hasattr(checkpointer, "setup"):
            await checkpointer.setup()
        logger.info("异步 SQLite Checkpointer 创建成功")
        return checkpointer
    except Exception:
        logger.exception("创建异步 SQLite Checkpointer 失败")
        return None


def _create_async_postgres_checkpointer(connection_string: str | None = None) -> Any:
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
    except Exception:
        logger.exception("创建异步 PostgreSQL Checkpointer 失败")
        return None


async def get_async_checkpointer(
    backend: str | None = None,
    connection_string: str | None = None,
    db_path: str | None = None,
) -> Any:
    backend = backend or getattr(settings, "checkpointer_backend", "sqlite")

    # AsyncPostgresSaver 内部的 asyncio.Lock 绑定到创建时的事件循环，
    # 必须按 loop_id 缓存独立实例，否则 "bound to a different event loop" 报错。
    # 关键改进：请求结束后必须调用 release_async_checkpointer() 释放旧实例的连接，
    # 避免连接泄漏。
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0

    cache_key = f"async:{backend}:{db_path or ''}:{connection_string or ''}:loop{loop_id}"

    with _cache_lock:
        if cache_key in _checkpointer_cache:
            _checkpointer_cache.move_to_end(cache_key)
            return _checkpointer_cache[cache_key]

    if backend == "postgres":
        cm = _create_async_postgres_checkpointer(connection_string)
        if cm is not None:
            # from_conn_string 返回异步上下文管理器，需要 __aenter__ 获取实际实例
            if hasattr(cm, "__aenter__"):
                checkpointer = await cm.__aenter__()
                # 保存上下文管理器引用，防止连接池被 GC 回收关闭
                _context_manager_refs[f"pg_async:{id(checkpointer)}"] = cm
            else:
                checkpointer = cm
            if hasattr(checkpointer, "setup"):
                await checkpointer.setup()
            with _cache_lock:
                _checkpointer_cache[cache_key] = checkpointer
            return checkpointer
        logger.warning("异步 PostgreSQL Checkpointer 创建失败，回退到异步 SQLite")
        return await get_async_checkpointer(backend="sqlite", db_path=db_path)
    elif backend == "sqlite":
        checkpointer = await _create_async_sqlite_checkpointer(db_path)
        if checkpointer is not None:
            with _cache_lock:
                _checkpointer_cache[cache_key] = checkpointer
            return checkpointer
        logger.warning("异步 SQLite Checkpointer 创建失败，回退到同步 SQLite")
        return get_checkpointer(backend="sqlite", db_path=db_path)
    else:
        return get_checkpointer(backend=backend)


async def release_async_checkpointer(
    backend: str | None = None,
    connection_string: str | None = None,
    db_path: str | None = None,
) -> None:
    """
    释放当前事件循环对应的异步 Checkpointer，关闭连接池。

    在流式请求结束时调用（loop.close() 之前），防止连接泄漏。
    由于每个请求创建新的 asyncio.new_event_loop()，请求结束后
    事件循环关闭，对应的 AsyncPostgresSaver 无法再使用，
    必须主动关闭其连接池释放 PostgreSQL 连接。
    """
    backend = backend or getattr(settings, "checkpointer_backend", "sqlite")
    if backend != "postgres":
        return

    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0

    cache_key = f"async:{backend}:{db_path or ''}:{connection_string or ''}:loop{loop_id}"

    with _cache_lock:
        checkpointer = _checkpointer_cache.pop(cache_key, None)
        if checkpointer is None:
            return

    # 关闭异步上下文管理器，释放连接池
    cm_ref = _context_manager_refs.pop(f"pg_async:{id(checkpointer)}", None)
    if cm_ref is not None:
        try:
            await cm_ref.__aexit__(None, None, None)
            logger.debug(f"已释放异步 Checkpointer 连接: {cache_key}")
        except Exception as e:
            logger.debug(f"释放异步 Checkpointer 连接失败 ({cache_key}): {e}")
    elif hasattr(checkpointer, "close"):
        try:
            checkpointer.close()
        except Exception as e:
            logger.debug(f"关闭异步 Checkpointer 失败 ({cache_key}): {e}")


def _create_sqlite_checkpointer(db_path: str | None = None) -> Any:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        if db_path is None:
            data_dir = getattr(settings, "data_dir", None) or os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data"
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
            if hasattr(checkpointer, "setup"):
                checkpointer.setup()
            logger.info("SQLite Checkpointer 创建成功（独立连接）")
            return checkpointer
        except TypeError:
            pass

        checkpointer = SqliteSaver.from_conn_string(db_path)

        if hasattr(checkpointer, "setup"):
            checkpointer.setup()
            logger.info("SQLite Checkpointer 创建成功（直接 setup）")
            return checkpointer

        if hasattr(checkpointer, "__enter__"):
            checkpointer = checkpointer.__enter__()
            atexit.register(checkpointer.__exit__, None, None, None)
            if hasattr(checkpointer, "setup"):
                checkpointer.setup()
            logger.info("SQLite Checkpointer 创建成功（上下文管理器模式）")
            return checkpointer

        logger.info("SQLite Checkpointer 创建成功（无需 setup）")
        return checkpointer

    except ImportError:
        logger.warning(
            "langgraph-checkpoint-sqlite 未安装，回退到 MemorySaver。安装命令: pip install langgraph-checkpoint-sqlite"
        )
        return _create_memory_checkpointer()
    except Exception:
        logger.exception("创建 SQLite Checkpointer 失败，回退到 MemorySaver")
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
            prefix = conn_str.split("://", maxsplit=1)[0] + "://"
            rest = conn_str.split("://")[1]
            if ":" in rest.split("@")[0]:
                user = rest.split(":")[0]
                after_at = rest.split("@")[1]
                return f"{prefix}{user}:****@{after_at}"
        except (IndexError, ValueError):
            pass
    return "***"


def get_store(backend: str | None = None) -> Any:
    backend = backend or getattr(settings, "store_backend", "memory")
    cache_key = f"store:{backend}"

    with _cache_lock:
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
        with _cache_lock:
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
        logger.warning("langgraph.store.memory.InMemoryStore 不可用，请升级 langgraph>=0.2.0")
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
            "langgraph-store-postgres 未安装，回退到 InMemoryStore。安装命令: pip install langgraph-store-postgres"
        )
        return _create_memory_store()

    connection_string = _build_connection_string()

    logger.info(f"创建 PostgreSQL Store: {_mask_connection_string(connection_string)}")

    try:
        cm = PostgresStore.from_conn_string(connection_string)
        # from_conn_string 返回上下文管理器，需要 __enter__ 获取实际实例
        if hasattr(cm, "__enter__"):
            store = cm.__enter__()
            # 保存上下文管理器引用，防止连接池被 GC 回收关闭
            _context_manager_refs[f"pg_store:{id(store)}"] = cm
            atexit.register(cm.__exit__, None, None, None)
        else:
            store = cm
        if hasattr(store, "setup"):
            store.setup()
        logger.info("PostgreSQL Store 创建成功")
        return store
    except Exception:
        logger.exception("创建 PostgreSQL Store 失败，回退到 InMemoryStore")
        return _create_memory_store()


def ensure_store(instance) -> Any:
    if getattr(instance, "_store", None) is not None:
        return instance._store
    try:
        instance._store = get_store()
        return instance._store
    except Exception as e:
        logger.warning(f"Store 不可用: {e}")
        return None


async def delete_thread_checkpoints(thread_id: str) -> bool:
    """
    删除指定 thread_id 的所有 checkpoint 数据

    在用户删除聊天会话时调用，清理 PostgreSQL/SQLite 中的残留数据。

    Args:
        thread_id: 会话 ID（对应 LangGraph 的 thread_id）

    Returns:
        True 表示删除成功或无需清理，False 表示删除失败
    """
    if not thread_id:
        return True

    try:
        # 优先使用异步 checkpointer（流式场景）
        checkpointer = await get_async_checkpointer()
        if checkpointer is not None and hasattr(checkpointer, "adelete_thread"):
            await checkpointer.adelete_thread(thread_id=thread_id)
            logger.info(f"异步 Checkpointer 已删除 thread={thread_id} 的 checkpoint 数据")
            return True

        # 回退到同步 checkpointer
        checkpointer = get_checkpointer()
        if checkpointer is not None and hasattr(checkpointer, "delete_thread"):
            checkpointer.delete_thread(thread_id=thread_id)
            logger.info(f"同步 Checkpointer 已删除 thread={thread_id} 的 checkpoint 数据")
            return True

        logger.warning(f"Checkpointer 不支持 delete_thread，thread={thread_id} 的数据未清理")
        return False
    except Exception:
        logger.exception(f"删除 thread={thread_id} 的 checkpoint 数据失败")
        return False


async def delete_thread_store_data(user_id: int, thread_id: str | None = None) -> bool:
    """
    删除 Store 中指定会话或用户的所有长期记忆数据

    Store 中的 namespace 结构:
    - (user_id, "documents"): 附件文档记忆
    - (user_id, "session_contexts"): 会话上下文摘要
    - (user_id, "knowledge_graph"): 知识图谱
    - (user_id, "compression_state"): 压缩状态
    - (user_id, "mcp_contexts"): MCP 上下文

    Args:
        user_id: 用户 ID
        thread_id: 可选，指定会话 ID 时只清理该会话相关数据

    Returns:
        True 表示删除成功或无需清理
    """
    try:
        store = get_store()
        if store is None:
            return True

        namespaces = [
            (str(user_id), "documents"),
            (str(user_id), "session_contexts"),
            (str(user_id), "knowledge_graph"),
            (str(user_id), "compression_state"),
            (str(user_id), "mcp_contexts"),
        ]

        for namespace in namespaces:
            try:
                items = store.search(namespace)
                for item in items:
                    # 如果指定了 thread_id，只删除该会话的数据
                    if thread_id and str(thread_id) != str(item.key):
                        continue
                    store.delete(namespace, item.key)
            except Exception as e:
                logger.debug(f"清理 Store namespace={namespace} 失败: {e}")

        logger.info(f"Store 数据已清理: user_id={user_id}, thread_id={thread_id}")
        return True
    except Exception:
        logger.exception(f"清理 Store 数据失败: user_id={user_id}, thread_id={thread_id}")
        return False


async def delete_user_all_data(user_id: int) -> bool:
    """
    删除用户的所有 checkpoint 和 Store 数据（用户注销时调用）

    注意：此操作不经过 cross_app 的"双方都删才清理"守卫，因为用户注销时
    所有数据都应被清理。如果未来支持"用户恢复"功能，需在此路径中增加
    research guard 检查以避免误删研究任务的 Store 数据。

    Args:
        user_id: 用户 ID

    Returns:
        True 表示删除成功
    """
    try:
        # 获取用户所有会话的 session_id
        from django.apps import apps

        ChatSession = apps.get_model("chat", "ChatSession")
        session_ids = list(ChatSession.objects.filter(user_id=user_id).values_list("session_id", flat=True))

        # 清理每个会话的 checkpoint
        for session_id in session_ids:
            await delete_thread_checkpoints(str(session_id))

        # 清理 Store 中用户的所有数据
        await delete_thread_store_data(user_id)

        logger.info(f"用户 {user_id} 的所有 checkpoint/Store 数据已清理（{len(session_ids)} 个会话）")
        return True
    except Exception:
        logger.exception(f"清理用户 {user_id} 的所有数据失败")
        return False
