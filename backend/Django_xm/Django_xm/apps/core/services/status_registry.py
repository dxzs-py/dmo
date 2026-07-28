"""数据库状态提供者注册表

允许业务 app（knowledge、cache_manager 等）向 ``core`` 注册状态查询接口，
而不违反 ``core`` 不依赖业务 app 的分层约束（Task 15.3）。

设计动机：
    ``DatabaseMonitor`` 原在 ``core/services/db_monitor.py`` 中直接导入
    ``knowledge.services.cross_app``、``ai_engine.config``、
    ``cache_manager.services.cache_service``，违反 ``core → 业务 app`` 分层。
    通过注册表模式，``core`` 定义抽象接口，业务 app 实现并注册，
    ``DatabaseMonitor.get_database_overview()`` 遍历注册表聚合状态。

使用方式：
    # knowledge/apps.py ready() 中注册
    from Django_xm.apps.core.services.status_registry import register_status_provider
    from ..services.status_provider import VectorStoreStatusProvider  # knowledge app 内
    register_status_provider(VectorStoreStatusProvider())

    # core/services/db_monitor.py 中查询
    from .status_registry import get_all_status_providers
    for provider in get_all_status_providers():
        status = provider.get_status()
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

_providers: list[DatabaseStatusProvider] = []
_provider_names: set = set()
_lock = threading.Lock()


class DatabaseStatusProvider(ABC):
    """数据库状态提供者抽象基类。

    业务 app 实现此类并注册到 ``status_registry``，
    供 ``DatabaseMonitor.get_database_overview()`` 聚合调用。
    """

    @abstractmethod
    def get_name(self) -> str:
        """返回提供者名称（如 'vector_store'、'redis'），用作 overview 的 key。"""

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """返回该组件的状态字典。应包含 'connection' 字段（'healthy'/'unhealthy'）。"""


def register_status_provider(provider: DatabaseStatusProvider) -> None:
    """注册状态提供者。

    幂等：同名提供者不会重复注册。
    """
    with _lock:
        name = provider.get_name()
        if name in _provider_names:
            logger.debug(f"状态提供者已注册，跳过: {name}")
            return
        _providers.append(provider)
        _provider_names.add(name)
        logger.debug(f"状态提供者已注册: {name}")


def get_all_status_providers() -> list[DatabaseStatusProvider]:
    """获取所有已注册的状态提供者。返回新列表，调用方可安全修改。"""
    with _lock:
        return list(_providers)


def get_status_by_name(name: str) -> dict[str, Any]:
    """按名称查询某个提供者的状态。

    Args:
        name: 提供者名称（如 'vector_store'、'redis'）

    Returns:
        状态字典；若提供者不存在，返回 {'connection': 'unregistered', 'error': '...'}
    """
    with _lock:
        for provider in _providers:
            if provider.get_name() == name:
                return provider.get_status()
    return {
        'connection': 'unregistered',
        'error': f"未注册的状态提供者: {name}",
    }


def clear_status_providers() -> None:
    """清空注册表（仅供测试使用）。"""
    with _lock:
        _providers.clear()
        _provider_names.clear()
