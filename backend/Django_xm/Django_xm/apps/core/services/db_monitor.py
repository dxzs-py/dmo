"""
数据库监控服务

提供 PostgreSQL、VectorStore、Redis 状态查询功能。
"""

import logging
import os
from pathlib import Path

from django.db import connections

logger = logging.getLogger(__name__)


class DatabaseMonitor:
    """数据库监控类，封装各数据源状态查询逻辑"""

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
            'connected': False,
            'connection': 'disconnected',
            'backend': 'PostgreSQL',
            'error': None,
        }

        try:
            with connections['default'].cursor() as cursor:
                # 版本
                cursor.execute("SELECT version()")
                row = cursor.fetchone()
                status['version'] = row[0] if row else 'unknown'

                # 数据库名称
                cursor.execute("SELECT current_database()")
                row = cursor.fetchone()
                status['database_name'] = row[0] if row else 'unknown'

                # 活跃连接数
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                )
                row = cursor.fetchone()
                status['threads_connected'] = row[0] if row else 0

                # 总查询数
                cursor.execute(
                    "SELECT sum(xact_commit + xact_rollback) "
                    "FROM pg_stat_database WHERE datname = current_database()"
                )
                row = cursor.fetchone()
                status['questions'] = int(row[0]) if row and row[0] else 0

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
                    tables.append({
                        'name': row[0],
                        'data_size': data_size,
                        'index_size': index_size,
                        'total_size': total_size,
                        'rows': row_count,
                    })

                status['connected'] = True
                status['connection'] = 'healthy'
                status['database_size'] = db_size
                status['total_size_mb'] = round(db_size / (1024 * 1024), 2)
                status['data_size'] = total_data_size
                status['index_size'] = total_index_size
                status['tables'] = tables
                status['table_count'] = len(tables)
        except Exception as e:
            status['error'] = str(e)

        return status

    @staticmethod
    def get_vector_store_status():
        """获取向量存储状态"""
        try:
            from Django_xm.apps.knowledge.services.cross_app import get_index_manager
            from Django_xm.apps.ai_engine.config import settings as app_cfg

            manager = get_index_manager()
            base_path = manager.base_path

            if not base_path.exists():
                base_path.mkdir(parents=True, exist_ok=True)

            all_indices = manager.list_indexes()
            index_count = len(all_indices)

            total_size = 0
            index_info = []

            for idx in all_indices:
                try:
                    idx_name = idx.get('name')
                    if not idx_name:
                        continue

                    idx_path = base_path / idx_name
                    idx_size = 0

                    if idx_path.exists():
                        for root, dirs, files in os.walk(idx_path):
                            for file in files:
                                try:
                                    file_path = Path(root) / file
                                    idx_size += file_path.stat().st_size
                                except (OSError, Exception):
                                    pass

                    total_size += idx_size

                    index_info.append({
                        'name': idx_name,
                        'original_name': idx.get('name', idx_name),
                        'size': idx_size,
                        'size_human': DatabaseMonitor.format_size(idx_size),
                        'created_at': idx.get('created_at', ''),
                        'updated_at': idx.get('updated_at', ''),
                        'num_documents': idx.get('num_documents', 0),
                    })
                except Exception as idx_err:
                    logger.warning(f"处理索引 {idx.get('name')} 时出错: {idx_err}")
                    continue

            return {
                'backend': app_cfg.vector_store_type,
                'base_path': str(base_path),
                'connection': 'healthy',
                'index_count': index_count,
                'total_size': total_size,
                'total_size_human': DatabaseMonitor.format_size(total_size),
                'indices': index_info,
            }
        except Exception as e:
            logger.error(f"获取向量存储状态失败: {e}", exc_info=True)
            backend_type = 'unknown'
            try:
                from Django_xm.apps.ai_engine.config import settings as app_cfg
                backend_type = app_cfg.vector_store_type
            except (AttributeError, Exception):
                pass

            base_path_str = ''
            try:
                from Django_xm.apps.ai_engine.config import settings as app_cfg
                base_path_str = str(Path(app_cfg.vector_store_path))
            except (AttributeError, Exception):
                pass

            return {
                'backend': backend_type,
                'base_path': base_path_str,
                'connection': 'unhealthy',
                'index_count': 0,
                'total_size': 0,
                'total_size_human': '0 B',
                'indices': [],
                'error': str(e),
            }

    @staticmethod
    def get_redis_info():
        """获取 Redis 信息"""
        from Django_xm.apps.cache_manager.services.cache_service import get_redis_info as _get_redis_info
        return _get_redis_info()

    @staticmethod
    def _get_redis_client():
        """获取 Redis 客户端"""
        from Django_xm.apps.cache_manager.services.cache_service import get_redis_client as _get_redis_client
        return _get_redis_client()

    @staticmethod
    def get_database_overview():
        """获取数据库总览（PostgreSQL + VectorStore + Redis）"""
        postgresql_status = DatabaseMonitor.get_postgresql_status()
        vector_status = DatabaseMonitor.get_vector_store_status()
        redis_info = DatabaseMonitor.get_redis_info()

        redis_status = {
            'backend': 'Redis',
            'connection': 'healthy' if redis_info else 'unhealthy',
        }
        if redis_info:
            redis_status['version'] = redis_info.get('redis_version', '-')
            redis_status['used_memory_human'] = redis_info.get('used_memory_human', '-')
            redis_status['connected_clients'] = redis_info.get('connected_clients', 0)
            db_info = redis_info.get('db0', {})
            if isinstance(db_info, dict):
                redis_status['total_keys'] = db_info.get('keys', 0)

        return {
            'postgresql': postgresql_status,
            'vector_store': vector_status,
            'redis': redis_status,
        }
