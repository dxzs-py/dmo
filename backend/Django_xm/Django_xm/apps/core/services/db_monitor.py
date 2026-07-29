"""数据库监控服务

提供 PostgreSQL 状态查询功能。

归属说明（Task 15.3）：
    原模块还包含 ``get_vector_store_status()``（依赖 knowledge）和
    ``get_redis_info()``（依赖 cache_manager），违反 ``core`` 不依赖
    业务 app 的分层约束。这两个方法已迁入对应 app 的 ``status_provider.py``，
    通过 ``status_registry`` 注册供 ``get_database_overview()`` 聚合调用。

    ``ai_engine.config`` 的导入也已移除（原用于读取 vector_store_type），
    由各业务 app 的 status_provider 自行处理。
"""

import logging

from django.db import connections

from .status_registry import get_all_status_providers

logger = logging.getLogger(__name__)


class DatabaseMonitor:
    """数据库监控类，封装 PostgreSQL 状态查询与多源状态聚合。"""

    @staticmethod
    def format_size(size_bytes):
        """将字节数格式化为可读字符串"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    @staticmethod
    def get_postgresql_status():
        """获取 PostgreSQL 数据库状态"""
        status = {
            "connected": False,
            "connection": "disconnected",
            "backend": "PostgreSQL",
            "error": None,
        }

        try:
            with connections["default"].cursor() as cursor:
                # 版本
                cursor.execute("SELECT version()")
                row = cursor.fetchone()
                status["version"] = row[0] if row else "unknown"

                # 数据库名称
                cursor.execute("SELECT current_database()")
                row = cursor.fetchone()
                status["database_name"] = row[0] if row else "unknown"

                # 活跃连接数
                cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
                row = cursor.fetchone()
                status["threads_connected"] = row[0] if row else 0

                # 总查询数
                cursor.execute(
                    "SELECT sum(xact_commit + xact_rollback) FROM pg_stat_database WHERE datname = current_database()"
                )
                row = cursor.fetchone()
                status["questions"] = int(row[0]) if row and row[0] else 0

                # 数据库大小
                cursor.execute("SELECT pg_database_size(current_database())")
                row = cursor.fetchone()
                db_size = row[0] if row else 0

                # 表统计 — 单条 SQL 获取所有表的大小，避免 N+1 查询
                cursor.execute("""
                    SELECT s.relname,
                           pg_relation_size(c.oid) AS data_size,
                           pg_indexes_size(c.oid) AS index_size,
                           pg_total_relation_size(c.oid) AS total_size,
                           s.n_live_tup AS row_count
                    FROM pg_stat_user_tables s
                    JOIN pg_class c ON c.relname = s.relname
                    ORDER BY pg_total_relation_size(c.oid) DESC
                """)
                tables = []
                total_data_size = 0
                total_index_size = 0
                for row in cursor.fetchall():
                    data_size = row[1] or 0
                    index_size = row[2] or 0
                    total_size = row[3] or 0
                    row_count = row[4] or 0
                    total_data_size += data_size
                    total_index_size += index_size
                    tables.append(
                        {
                            "name": row[0],
                            "data_size": data_size,
                            "index_size": index_size,
                            "total_size": total_size,
                            "rows": row_count,
                        }
                    )

                status["connected"] = True
                status["connection"] = "healthy"
                status["database_size"] = db_size
                status["total_size_mb"] = round(db_size / (1024 * 1024), 2)
                status["data_size"] = total_data_size
                status["index_size"] = total_index_size
                status["tables"] = tables
                status["table_count"] = len(tables)
        except Exception as e:
            status["error"] = str(e)

        return status

    @staticmethod
    def get_database_overview():
        """获取数据库总览（PostgreSQL + 所有已注册状态提供者）

        状态提供者通过 ``status_registry`` 注册（Task 15.3）：
        - ``knowledge`` 注册 ``VectorStoreStatusProvider``
        - ``cache_manager`` 注册 ``RedisStatusProvider``
        """
        overview = {
            "postgresql": DatabaseMonitor.get_postgresql_status(),
        }

        for provider in get_all_status_providers():
            try:
                overview[provider.get_name()] = provider.get_status()
            except Exception as e:
                overview[provider.get_name()] = {
                    "connection": "unhealthy",
                    "error": str(e),
                }
                logger.warning(
                    f"状态提供者 {provider.get_name()} 查询失败: {e}",
                    exc_info=True,
                )

        return overview
