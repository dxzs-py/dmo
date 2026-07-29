"""Cache Manager app 状态提供者

将 Redis 状态查询逻辑从 ``core/services/db_monitor.py`` 迁入 ``cache_manager``
（Task 15.3：消除 ``core → cache_manager`` 分层违规）。

依赖方向：
    - ``cache_manager`` → ``core``（注册到 core 定义的接口，正确）
"""

from __future__ import annotations

import logging
from typing import Any

from Django_xm.apps.core.services.status_registry import DatabaseStatusProvider

logger = logging.getLogger(__name__)


class RedisStatusProvider(DatabaseStatusProvider):
    """Redis 状态提供者"""

    def get_name(self) -> str:
        return "redis"

    def get_status(self) -> dict[str, Any]:
        """获取 Redis 状态"""
        from Django_xm.apps.cache_manager.services.cache_service import get_redis_info

        redis_info = get_redis_info()

        redis_status: dict[str, Any] = {
            "backend": "Redis",
            "connection": "healthy" if redis_info else "unhealthy",
        }
        if redis_info:
            redis_status["version"] = redis_info.get("redis_version", "-")
            redis_status["used_memory_human"] = redis_info.get("used_memory_human", "-")
            redis_status["connected_clients"] = redis_info.get("connected_clients", 0)
            db_info = redis_info.get("db0", {})
            if isinstance(db_info, dict):
                redis_status["total_keys"] = db_info.get("keys", 0)

        return redis_status
