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
import itertools
import os
import threading
import weakref
from collections import OrderedDict
from typing import Any

from Django_xm.apps.ai_engine.config import settings
from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

_checkpointer_cache: OrderedDict = OrderedDict()
_store_cache: OrderedDict = OrderedDict()
# 保存上下文管理器引用，防止连接池被 GC 回收关闭
# - pg_sync / pg_store：值为同步上下文管理器（cm）；
# - pg_async（round3 2.7）：值为 {"cm": cm, "loop_ref": weakref}，记录原创建
#   loop 以便淘汰/关闭时调度回正确 loop（跨 loop __aexit__ 无效）。
_context_manager_refs: dict = {}
_CACHE_MAXSIZE = 64
_cache_lock = threading.Lock()
# 异步创建锁（per cache_key，含 loop token 隔离）：消除同 loop 并发首访竞态。
# 同一 cache_key 的锁只会在同一事件循环上 await（cache_key 含 loop token），
# asyncio.Lock 首次 await 时绑定当前 loop，跨 loop 污染不会发生；
# 创建/写入缓存全程持锁 + 锁内双检，防止重复建池与 cm_ref 残留泄漏。
_async_create_locks: dict[str, asyncio.Lock] = {}

# ── round3 2.7：非 postgres 后端一次性 loop 专用 ephemeral 注册表 ──
# 非 postgres 异步 checkpointer（sqlite 等）不再进入全局 LRU：
# 1) 旧实现 release_async_checkpointer 对非 postgres 直接 return，一次性 loop
#    （子代理独立线程）的 sqlite 实例在 LRU 中堆积，淘汰回调对绑定已死 loop
#    的实例基本无效（跨 loop 关闭不可靠）→ 连接泄漏；
# 2) ephemeral 注册表按 loop token 归组：同 loop 内多次获取共享同一实例
#    （多图共享 checkpoint 语义不变），release_async_checkpointer 在同一 loop
#    上关闭（release 本就 await 于该 loop），用完即弃；
# 3) loop 死亡未 release（异常路径）时，条目随 sweep（loop 弱引用失效）丢弃，
#    实例交由 GC 兜底。成本说明：一次性 loop 每条本地 sqlite 连接（廉价），
#    远小于 PG 连接池，不缓存是正确性优先的取舍。
_ephemeral_checkpointers: dict[str, dict] = {}  # cache_key -> {"checkpointer", "loop_ref"}

# loop → cache token 弱引用稳定映射（round3 2.7）：id(loop) 在 loop 关闭回收后
# 会被新 loop 复用，旧 cache_key 将命中绑定已死 loop 的实例；token 按对象
# 稳定、loop 回收后不复用。
_loop_tokens: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_loop_token_counter = itertools.count(1)


def _loop_cache_token(loop: asyncio.AbstractEventLoop | None) -> str:
    """为事件循环生成进程内唯一缓存 token（round3 2.7）。

    同一 loop 对象恒返回同一 token；loop 被回收后 token 不再复用（新 loop
    取新序号），彻底消除 id(loop) 复用导致旧缓存误命中。loop 为 None
    （无运行中 loop 的同步上下文）返回固定 token（历史 loop_id=0 同语义）。
    弱引用不可用（对象不支持 weakref 的防御分支）退化为
    "id{id}:closed{bool}" 组合校验。
    """
    if loop is None:
        return "noloop"
    with _cache_lock:
        try:
            token = _loop_tokens.get(loop)
            if token is None:
                token = f"loop{next(_loop_token_counter)}"
                _loop_tokens[loop] = token
            return token
        except TypeError:
            return f"id{id(loop)}:closed{bool(loop.is_closed())}"


def _register_ephemeral_checkpointer(cache_key: str, checkpointer: Any, loop) -> None:
    """登记非 postgres 异步 checkpointer 到 ephemeral 注册表（顺手清扫死 loop 条目）。"""
    with _cache_lock:
        for key in [k for k, v in _ephemeral_checkpointers.items() if v["loop_ref"]() is None]:
            # loop 已死亡且未走 release（异常路径）：丢弃登记，连接交 GC 兜底
            _ephemeral_checkpointers.pop(key, None)
            logger.debug(f"已清扫死 loop 的 ephemeral checkpointer 条目: {key}")
        _ephemeral_checkpointers[cache_key] = {
            "checkpointer": checkpointer,
            "loop_ref": weakref.ref(loop) if loop is not None else None,
        }


async def _close_ephemeral_checkpointer(checkpointer: Any) -> None:
    """关闭 ephemeral（非 postgres）异步 checkpointer——必须在其绑定 loop 上调用。"""
    conn = getattr(checkpointer, "conn", None)  # AsyncSqliteSaver.conn（aiosqlite 连接）
    if conn is not None and hasattr(conn, "aclose"):
        try:
            await conn.aclose()
            return
        except Exception as e:
            logger.debug(f"关闭 ephemeral checkpointer 连接失败（aclose）: {e}")
    if conn is not None and hasattr(conn, "close"):
        try:
            await conn.close()
            return
        except Exception as e:
            logger.debug(f"关闭 ephemeral checkpointer 连接失败（close）: {e}")
    if hasattr(checkpointer, "close"):
        try:
            result = checkpointer.close()
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.debug(f"关闭 ephemeral checkpointer 失败: {e}")


def _get_async_create_lock(cache_key: str) -> asyncio.Lock:
    """取（或创建）cache_key 对应的异步创建锁（dict 操作受 _cache_lock 保护）"""
    with _cache_lock:
        lock = _async_create_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            _async_create_locks[cache_key] = lock
        return lock

# 常驻主事件循环 id（SessionManager 所在执行循环）：
# 主 loop 上可并发多个会话共享同一 PG 连接池（cache_key 按 loop_id 隔离），
# 任一会话结束（尤其被强制终止）都不应关闭整个池——否则其他挂起/运行中会话
# 恢复重入 astream 时复用已关闭连接崩溃（问题L）。
# 主 loop 连接池由进程退出时 close_all_checkpointers()（atexit）统一关闭。
_main_loop_id: int | None = None


def register_main_loop() -> None:
    """注册常驻主事件循环（SessionManager 启动时调用，幂等）。

    主 loop 上的异步 checkpointer 连接池常驻，不随会话释放；
    子代理等独立一次性 loop（loop_id 不同）仍按原逻辑正常释放。
    """
    global _main_loop_id  # noqa: PLW0603 - 模块级单例惰性初始化
    try:
        _main_loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        _main_loop_id = None


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
        # （round3 2.7：值为 {"cm", "loop_ref"} 记录，关闭调度回原创建 loop）
        record = _context_manager_refs.pop(f"pg_async:{id(checkpointer)}", None)
        if record is not None:
            _close_async_cm_record(record, cache_key)
            return

        # 尝试直接关闭连接
        if hasattr(checkpointer, "close"):
            checkpointer.close()
            logger.debug(f"已关闭 checkpointer: {cache_key}")
    except Exception as e:
        logger.warning(f"关闭 checkpointer 失败 ({cache_key}): {e}")


def _close_async_cm_record(record: dict, cache_key: str) -> None:
    """在原创建 loop 上关闭异步 checkpointer 上下文管理器（round3 2.7）。

    旧实现（:85-88）在「淘汰时碰巧正在运行的任意 loop」上 create_task
    __aexit__——跨 loop 关闭无效（连接绑定原 loop），任务静默失败泄漏。
    修复：按记录的 loop_ref 调度——
    - 原 loop 存活且为当前 loop：create_task 调度（同 loop，fire-and-forget）；
    - 原 loop 存活但当前线程/loop 不同：run_coroutine_threadsafe 到原 loop；
    - 原 loop 已关闭/被回收：连接绑定死 loop 无法优雅关闭（asyncio 资源与
      loop 绑定的框架边界），error 留痕，连接由进程退出回收。
    """
    cm = record.get("cm") if isinstance(record, dict) else None
    loop_ref = record.get("loop_ref") if isinstance(record, dict) else None
    loop = loop_ref() if loop_ref is not None else None
    if cm is None or not hasattr(cm, "__aexit__"):
        return
    coro = cm.__aexit__(None, None, None)
    try:
        if loop is not None and not loop.is_closed():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                task = loop.create_task(coro)
                task.add_done_callback(lambda _t: None)
            else:
                asyncio.run_coroutine_threadsafe(coro, loop)
            logger.debug(f"已调度异步 checkpointer 关闭到原创建 loop: {cache_key}")
            return
    except Exception as e:
        logger.debug(f"调度异步 checkpointer 关闭失败 ({cache_key}): {e}")
    coro.close()  # 丢弃未执行协程，避免 "coroutine was never awaited" 告警
    logger.warning(
        f"异步 checkpointer 的原事件循环已关闭，连接无法优雅关闭"
        f"（依赖进程退出回收）: {cache_key}"
    )


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
        # ephemeral（非 postgres）条目：其连接绑定各自 loop，进程退出随 loop/
        # 进程消亡回收——仅清登记，不做跨 loop 关闭（无效且可能报错）。
        _ephemeral_checkpointers.clear()

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
    构建 PostgreSQL 连接字符串（解析收敛至 common/connections.py 单一来源）

    优先级：环境变量 CHECKPOINTER_POSTGRES_URI > settings 配置 > 拼接

    安全措施：
    - 优先从环境变量读取完整连接字符串，避免密码出现在代码中
    - 日志中始终遮蔽密码（_mask_connection_string）
    """
    from Django_xm.common.connections import get_postgres_connection_string

    return get_postgres_connection_string(uri_override=getattr(settings, "checkpointer_postgres_uri", None))


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
    # 必须按 loop 隔离实例，否则 "bound to a different event loop" 报错。
    # round3 2.7：loop 标识从 id(loop) 换为弱引用稳定 token——id 数值在
    # loop 关闭回收后会被新 loop 复用，旧 cache_key 会命中绑定已死 loop
    # 的实例（同 id 不同对象）。请求结束后必须调用 release_async_checkpointer()
    # 释放旧实例的连接，避免连接泄漏。
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    loop_token = _loop_cache_token(loop)
    cache_key = f"async:{backend}:{db_path or ''}:{connection_string or ''}:{loop_token}"

    # 快速路径：已有实例直接返回（postgres → LRU；非 postgres → ephemeral 注册表）
    with _cache_lock:
        if backend != "postgres" and cache_key in _ephemeral_checkpointers:
            return _ephemeral_checkpointers[cache_key]["checkpointer"]
        if cache_key in _checkpointer_cache:
            _checkpointer_cache.move_to_end(cache_key)
            return _checkpointer_cache[cache_key]

    # 并发首访：同 loop 的多个协程同时 miss 时，per-key 锁保证只有一个协程
    # 执行创建（A16）。禁止用同步 _cache_lock 跨 await——会导致整个进程的
    # 事件循环阻塞；asyncio.Lock 仅在该 cache_key 所属 loop 上 await，安全。
    async with _get_async_create_lock(cache_key):
        # 双检：等待期间其他协程可能已完成创建
        with _cache_lock:
            if backend != "postgres" and cache_key in _ephemeral_checkpointers:
                return _ephemeral_checkpointers[cache_key]["checkpointer"]
            if cache_key in _checkpointer_cache:
                _checkpointer_cache.move_to_end(cache_key)
                return _checkpointer_cache[cache_key]

        if backend == "postgres":
            cm = _create_async_postgres_checkpointer(connection_string)
            if cm is not None:
                # from_conn_string 返回异步上下文管理器，需要 __aenter__ 获取实际实例
                if hasattr(cm, "__aenter__"):
                    checkpointer = await cm.__aenter__()
                    # 保存上下文管理器引用（含原创建 loop 弱引用），
                    # 防止连接池被 GC 回收关闭 + 淘汰/关闭时调度回正确 loop
                    _context_manager_refs[f"pg_async:{id(checkpointer)}"] = {
                        "cm": cm,
                        "loop_ref": weakref.ref(loop) if loop is not None else None,
                    }
                else:
                    checkpointer = cm
                if hasattr(checkpointer, "setup"):
                    await checkpointer.setup()

                # Redis 热缓存装饰器（开关控制，D3）：包 AsyncPostgresSaver，PG 写优先。
                # 仅在 postgres 后端启用；关闭时直接返回裸 AsyncPostgresSaver。
                if getattr(settings, "checkpointer_redis_cache_enabled", False):
                    from Django_xm.apps.ai_engine.services.redis_async_client import get_async_redis_client
                    from Django_xm.apps.ai_engine.services.redis_cached_checkpointer import RedisCachedCheckpointer

                    inner = checkpointer
                    redis_client = get_async_redis_client()
                    checkpointer = RedisCachedCheckpointer(
                        inner=inner,
                        redis_client=redis_client,
                        ttl=getattr(settings, "checkpointer_redis_ttl", 1800),
                        lock_ttl=getattr(settings, "checkpointer_redis_lock_ttl", 5),
                    )
                    # 更新上下文管理器引用 key：release_async_checkpointer 按 checkpointer 的
                    # id 查找 cm_ref，包装后需把 inner 的 cm_ref 迁移到装饰器 id 下。
                    cm_record = _context_manager_refs.pop(f"pg_async:{id(inner)}", None)
                    if cm_record is not None:
                        _context_manager_refs[f"pg_async:{id(checkpointer)}"] = cm_record

                with _cache_lock:
                    _checkpointer_cache[cache_key] = checkpointer
                return checkpointer
            logger.warning("异步 PostgreSQL Checkpointer 创建失败，回退到异步 SQLite")
            return await get_async_checkpointer(backend="sqlite", db_path=db_path)
        elif backend == "sqlite":
            checkpointer = await _create_async_sqlite_checkpointer(db_path)
            if checkpointer is not None:
                # round3 2.7：非 postgres 后端不进全局 LRU（一次性 loop 用完即弃，
                # 由 release_async_checkpointer 在同一 loop 上关闭）；同 loop 多次
                # 获取仍共享同一实例（多图共享 checkpoint 语义不变）。
                _register_ephemeral_checkpointer(cache_key, checkpointer, loop)
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
    事件循环关闭，对应的异步 Checkpointer 无法再使用，
    必须主动关闭其连接池释放连接。

    round3 2.7 语义：
    - postgres：主 loop 常驻池跳过（问题L），一次性 loop pop LRU 并在当前
      loop 上 __aexit__（调用方保证 release await 于原 loop）；
    - 非 postgres（sqlite 等）：不再 early-return——从 ephemeral 注册表 pop
      并在当前 loop 上关闭连接（用完即弃），修复旧实现一次性 loop sqlite
      连接只进不出、依赖不可靠的 LRU 淘汰回调的泄漏；
    - 主 loop 上的非 postgres 实例同样常驻（多会话共享，释放会误关）。
    """
    backend = backend or getattr(settings, "checkpointer_backend", "sqlite")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    loop_id = id(loop) if loop is not None else 0

    cache_key = f"async:{backend}:{db_path or ''}:{connection_string or ''}:{_loop_cache_token(loop)}"

    # per-key 创建锁与后端无关，统一清理（A16：一次性 loop 的 cache_key 不同，
    # 不清理会让 _async_create_locks 随请求次数无限增长——内存泄漏）。
    with _cache_lock:
        _async_create_locks.pop(cache_key, None)

    # 主 loop 常驻连接池：不随单个会话释放（多会话共享同一连接池/实例，释放会
    # 误关其他挂起/运行中会话的连接，导致审批恢复崩溃，见问题L）。由进程退出统一关闭。
    # 仅一次性 loop（子代理独立线程等，loop_id 与主 loop 不同）按原逻辑释放。
    if _main_loop_id is not None and loop_id == _main_loop_id:
        logger.debug("主事件循环 checkpointer 常驻，跳过会话级释放（进程退出统一关闭）")
        return

    if backend == "postgres":
        with _cache_lock:
            checkpointer = _checkpointer_cache.pop(cache_key, None)

        # 关闭异步上下文管理器，释放连接池（release await 于原创建 loop，同 loop 关闭安全）
        if checkpointer is not None:
            record = _context_manager_refs.pop(f"pg_async:{id(checkpointer)}", None)
            cm = record.get("cm") if isinstance(record, dict) else record
            if cm is not None and hasattr(cm, "__aexit__"):
                try:
                    await cm.__aexit__(None, None, None)
                    logger.debug(f"已释放异步 Checkpointer 连接: {cache_key}")
                except Exception as e:
                    logger.debug(f"释放异步 Checkpointer 连接失败 ({cache_key}): {e}")
            elif hasattr(checkpointer, "close"):
                try:
                    checkpointer.close()
                except Exception as e:
                    logger.debug(f"关闭异步 Checkpointer 失败 ({cache_key}): {e}")

        # 释放 Redis 热缓存客户端连接池（若启用）。Redis 客户端独立于 checkpointer 缓存，
        # 即使 checkpointer 已释放，仍需关闭（幂等：pop 返回 None 无害）。
        if getattr(settings, "checkpointer_redis_cache_enabled", False):
            from Django_xm.apps.ai_engine.services.redis_async_client import release_async_redis_client

            await release_async_redis_client()
        return

    # 非 postgres（sqlite 等）：ephemeral 注册表用完即弃（round3 2.7）。
    with _cache_lock:
        entry = _ephemeral_checkpointers.pop(cache_key, None)
    if entry is not None:
        checkpointer = entry.get("checkpointer")
        if checkpointer is not None:
            await _close_ephemeral_checkpointer(checkpointer)
            logger.debug(f"已释放 ephemeral Checkpointer 连接: {cache_key}")


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


def _log_memory_store_fallback(reason: str) -> None:
    """InMemoryStore 回退日志升级（A18）。

    Store 承载跨会话记忆/知识图谱/文档记忆等长期数据，回退到内存存储意味着
    这些数据重启即丢：生产配置（DEBUG=False）下升级为 error 级并给出明确
    后果文案；dev（DEBUG=True）保留 debug 级避免噪音。
    """
    from django.conf import settings as django_settings

    message = (
        f"Store 回退到 InMemoryStore（{reason}）：跨会话记忆/知识图谱等长期记忆将不持久，"
        "服务重启后全部丢失。生产环境请安装 langgraph-store-postgres 并确认 store_backend=postgres。"
    )
    if getattr(django_settings, "DEBUG", False):
        logger.debug(message)
    else:
        logger.error(message)


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
        _log_memory_store_fallback("langgraph-store-postgres 未安装")
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
        _log_memory_store_fallback("PostgreSQL Store 创建失败")
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

        # 4.7 GDPR 出口接线：知识图谱走 ContextKnowledgeGraph.clear_graph——
        # 除 Store 内 (user_id, "knowledge_graph") 命名空间外，还需清除 4.6 引入的
        # 进程级图缓存（直接 store.delete 不会触发缓存失效，TTL 内可能读到已删除
        # 的图数据）。clear() 幂等，与上方命名空间级删除重复覆盖无害。
        # 跨会话记忆（(user_id, "session_contexts")）已由 delete_thread_store_data
        # 的命名空间清单覆盖，无需重复处理。
        try:
            from Django_xm.apps.context_manager.services.knowledge_graph import ContextKnowledgeGraph

            ContextKnowledgeGraph().clear_graph(user_id)
        except Exception:
            logger.warning(f"清除用户 {user_id} 知识图谱（含进程缓存）失败", exc_info=True)

        logger.info(f"用户 {user_id} 的所有 checkpoint/Store 数据已清理（{len(session_ids)} 个会话）")
        return True
    except Exception:
        logger.exception(f"清理用户 {user_id} 的所有数据失败")
        return False
