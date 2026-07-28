"""
数据库连接统一管理模块

提供跨场景的数据库连接生命周期管理，防止连接池耗尽
（FATAL: sorry, too many clients already）。

覆盖场景：
  1. HTTP 请求 (middleware)        → 请求结束后回收
  2. SSE 长连接 (async generator)  → 心跳时定期回收 + 流结束回收
  3. Celery 后台任务               → 任务入口/出口/异常时回收
  4. 应用关闭 (signal)             → 进程退出前强制关闭所有连接
  5. 信号处理器 (post_save 等)     → 处理器执行后回收
  6. 通用上下文管理器              → with 块结束后回收

架构：
  ┌─────────────────────────────────────────────┐
  │          DatabaseConnectionManager           │
  │  (统一接口：cleanup / force_close_all /      │
  │   get_connection_count / check_and_alert)    │
  ├─────────────────────────────────────────────┤
  │  DatabaseConnectionMiddleware  ← HTTP 请求  │
  │  db_connection_guard            ← 通用上下文  │
  │  db_task 装饰器                ← Celery 任务 │
  │  sse_utils (心跳回调)          ← SSE 长连接  │
  │  shutdown handler              ← 进程退出    │
  └─────────────────────────────────────────────┘
"""

import functools
import logging
import threading
import time
from contextlib import contextmanager

from django.db import close_old_connections, connections

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------
# 活跃连接数占 max_connections 的告警阈值百分比
WARNING_THRESHOLD_PCT = 0.7
CRITICAL_THRESHOLD_PCT = 0.9

# 连接数查询缓存时间（秒），避免频繁查询 pg_stat_activity
_COUNT_CACHE_TTL = 5

# ---------------------------------------------------------------------------
# 内部状态（线程安全）
# ---------------------------------------------------------------------------
_last_count_check = {"time": 0.0, "active": 0, "max_conn": 100}
_cleanup_call_count = 0
_count_lock = threading.Lock()
_stats_lock = threading.Lock()


class DatabaseConnectionManager:
    """
    数据库连接统一管理器

    所有连接回收操作均通过此单例方法执行，便于：
    - 统一日志记录
    - 统计调用频次
    - 监控告警
    - 错误隔离
    """

    # ----- 核心方法 -----

    @staticmethod
    def cleanup(source: str = "unknown"):
        """
        安全清理过期/失效数据库连接。

        close_old_connections() 只关闭超过 CONN_MAX_AGE 的空闲连接
        或已不可用的连接，不会中断活跃事务。

        Args:
            source: 调用来源标识，用于日志追踪
        """
        global _cleanup_call_count
        try:
            close_old_connections()
            with _count_lock:
                _cleanup_call_count += 1
                count = _cleanup_call_count
            logger.debug("[DBConn] cleanup from=%s total_calls=%d", source, count)
        except Exception as e:
            # 连接清理失败不应影响业务流程，仅记录告警
            logger.warning("[DBConn] cleanup failed from=%s error=%s", source, e)

    @staticmethod
    def force_close_all(source: str = "shutdown"):
        """
        强制关闭所有数据库连接。

        仅在应用关闭等极端场景使用，会中断所有活跃连接。
        """
        try:
            for conn in connections.all():
                try:
                    conn.close()
                except Exception:
                    pass
            logger.info("[DBConn] force_close_all from=%s", source)
        except Exception as e:
            logger.warning("[DBConn] force_close_all failed from=%s error=%s", source, e)

    # ----- 监控方法 -----

    @staticmethod
    def get_connection_count() -> dict:
        """
        获取当前数据库连接统计信息（带缓存）。

        Returns:
            {
                "active": 当前活跃连接数,
                "max_connections": PostgreSQL max_connections 设置值,
                "usage_pct": 使用率百分比,
                "cached": 是否使用缓存值,
            }
        """
        now = time.time()

        # 先用锁保护缓存读取
        with _stats_lock:
            cached = dict(_last_count_check)

        # 缓存未过期，直接返回
        if now - cached["time"] < _COUNT_CACHE_TTL:
            return {
                "active": cached["active"],
                "max_connections": cached["max_conn"],
                "usage_pct": round(cached["active"] / max(cached["max_conn"], 1) * 100, 1),
                "cached": True,
            }

        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
                active = cursor.fetchone()[0]

                cursor.execute("SHOW max_connections")
                max_conn = int(cursor.fetchone()[0])

            with _stats_lock:
                _last_count_check.update(time=now, active=active, max_conn=max_conn)

            return {
                "active": active,
                "max_connections": max_conn,
                "usage_pct": round(active / max(max_conn, 1) * 100, 1),
                "cached": False,
            }
        except Exception as e:
            logger.warning("[DBConn] get_connection_count failed: %s", e)
            with _stats_lock:
                cached = dict(_last_count_check)
            return {
                "active": cached["active"],
                "max_connections": cached["max_conn"],
                "usage_pct": round(cached["active"] / max(cached["max_conn"], 1) * 100, 1),
                "cached": True,
                "error": str(e),
            }

    @staticmethod
    def check_and_alert() -> str:
        """
        检查连接使用率并按级别告警。

        Returns:
            "normal" | "warning" | "critical"
        """
        stats = DatabaseConnectionManager.get_connection_count()
        usage_pct = stats["usage_pct"] / 100.0

        if usage_pct >= CRITICAL_THRESHOLD_PCT:
            logger.critical(
                "[DBConn] 连接池即将耗尽! active=%d/%d usage=%.1f%%",
                stats["active"], stats["max_connections"], stats["usage_pct"],
            )
            return "critical"

        if usage_pct >= WARNING_THRESHOLD_PCT:
            logger.warning(
                "[DBConn] 连接池使用率偏高 active=%d/%d usage=%.1f%%",
                stats["active"], stats["max_connections"], stats["usage_pct"],
            )
            return "warning"

        return "normal"

    @staticmethod
    def get_stats() -> dict:
        """
        获取连接管理统计信息，用于监控面板展示。

        Returns:
            包含连接数、使用率、清理次数等统计
        """
        stats = DatabaseConnectionManager.get_connection_count()
        with _count_lock:
            stats["cleanup_call_count"] = _cleanup_call_count
        return stats


# ---------------------------------------------------------------------------
# 便捷模块级函数（统一接口入口）
# ---------------------------------------------------------------------------

def db_cleanup(source: str = "unknown"):
    """清理过期连接 - 统一入口函数"""
    DatabaseConnectionManager.cleanup(source)


def db_force_close_all(source: str = "shutdown"):
    """强制关闭所有连接 - 统一入口函数"""
    DatabaseConnectionManager.force_close_all(source)


def db_check_and_alert() -> str:
    """检查连接使用率并告警 - 统一入口函数"""
    return DatabaseConnectionManager.check_and_alert()


def db_get_stats() -> dict:
    """获取连接统计 - 统一入口函数"""
    return DatabaseConnectionManager.get_stats()


# ---------------------------------------------------------------------------
# 上下文管理器
# ---------------------------------------------------------------------------

@contextmanager
def db_connection_guard(source: str = "context"):
    """
    数据库连接守卫上下文管理器。

    在 with 块结束后自动清理过期连接，异常时也确保清理。

    用法:
        with db_connection_guard("signal_handler"):
            # 执行数据库操作
            User.objects.filter(...)
        # 退出 with 块后自动清理

    Args:
        source: 调用来源标识
    """
    try:
        yield
    finally:
        DatabaseConnectionManager.cleanup(source)


# ---------------------------------------------------------------------------
# Celery 任务装饰器
# ---------------------------------------------------------------------------

def db_task(func=None, *, cleanup_on_start=True, cleanup_on_end=True):
    """
    Celery 任务数据库连接管理装饰器。

    确保任务执行前后正确管理数据库连接，防止 Celery worker
    长生命周期下的连接泄漏。应与 @shared_task 配合使用，
    放在 @shared_task 之后（即更靠近函数定义）。

    功能：
    1. 任务入口：清理可能残留的旧连接（可选）
    2. 任务出口：清理本次任务使用的连接（可选）
    3. 异常时：确保连接仍被清理
    4. 连接使用率检查与告警

    用法:
        @shared_task(bind=True, name='my.task')
        @db_task
        def my_task(self, **kwargs):
            ...

        # 或自定义清理策略
        @shared_task(bind=True, name='my.task')
        @db_task(cleanup_on_start=True, cleanup_on_end=True)
        def my_task(self, **kwargs):
            ...

    Args:
        func: 被装饰函数（无参装饰器用法）
        cleanup_on_start: 任务开始前是否清理连接（默认 True）
        cleanup_on_end: 任务结束后是否清理连接（默认 True）
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # 优先使用 Celery 设置的 task name（可能在外部赋值给 wrapper）
            # 其次使用原函数的 name 属性，最后 fallback 到 __name__
            task_name = getattr(wrapper, 'name', None) or getattr(fn, 'name', None) or fn.__name__

            if cleanup_on_start:
                DatabaseConnectionManager.cleanup(source=f"celery_start:{task_name}")

            try:
                result = fn(*args, **kwargs)
            except Exception:
                # 异常时仍需清理，防止连接悬挂
                if cleanup_on_end:
                    DatabaseConnectionManager.cleanup(source=f"celery_error:{task_name}")
                raise
            else:
                if cleanup_on_end:
                    DatabaseConnectionManager.cleanup(source=f"celery_end:{task_name}")

                # 任务完成后检查连接使用率
                DatabaseConnectionManager.check_and_alert()

                return result

        return wrapper

    if func is not None:
        # 无参调用：@db_task
        return decorator(func)
    # 有参调用：@db_task(cleanup_on_start=False)
    return decorator
