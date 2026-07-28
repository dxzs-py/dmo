"""Knowledge app 状态提供者

将向量存储状态查询逻辑从 ``core/services/db_monitor.py`` 迁入 ``knowledge``
（Task 15.3：消除 ``core → knowledge`` 分层违规）。

依赖方向：
    - ``knowledge`` → ``core``（注册到 core 定义的接口，正确）
    - ``knowledge`` → ``ai_engine.config``（读取向量库配置，正确）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from Django_xm.apps.core.services.db_monitor import DatabaseMonitor
from Django_xm.apps.core.services.status_registry import DatabaseStatusProvider

logger = logging.getLogger(__name__)


class VectorStoreStatusProvider(DatabaseStatusProvider):
    """向量存储状态提供者"""

    def get_name(self) -> str:
        return "vector_store"

    def get_status(self) -> dict[str, Any]:
        """获取向量存储状态"""
        try:
            from Django_xm.apps.ai_engine.config import settings as app_cfg
            from Django_xm.apps.knowledge.services.cross_app import get_index_manager

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
            base_path_str = ''
            try:
                from Django_xm.apps.ai_engine.config import settings as app_cfg
                backend_type = app_cfg.vector_store_type
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
