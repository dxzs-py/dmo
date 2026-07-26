"""Builder 注册中心

提供基于装饰器的自动注册机制，将 AgentType 映射到对应的 Builder 类。

用法::

    @register_builder(AgentType.BASE, AgentType.RAG)
    class BaseAgentBuilder:
        ...
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Type

from Django_xm.apps.agent_hub.config import AgentType

logger = logging.getLogger(__name__)

# 全局注册表：AgentType -> Builder 类
_builder_registry: Dict[AgentType, Type[Any]] = {}


def register_builder(*agent_types: AgentType):
    """类装饰器：将 Builder 类注册到一个或多个 AgentType。

    参数:
        *agent_types: 一个或多个 AgentType 枚举值。

    用法::

        @register_builder(AgentType.BASE, AgentType.RAG, AgentType.SAFE_RAG)
        class BaseAgentBuilder:
            ...

    重复注册同一 AgentType 会抛出 ValueError；传入非 AgentType 值抛出 TypeError。
    """
    if not agent_types:
        raise ValueError("register_builder 至少需要传入一个 AgentType")

    def decorator(cls: Type[Any]) -> Type[Any]:
        for agent_type in agent_types:
            if not isinstance(agent_type, AgentType):
                raise TypeError(
                    f"register_builder 仅接受 AgentType 枚举值，收到: {agent_type!r}"
                )
            if agent_type in _builder_registry:
                existing = _builder_registry[agent_type]
                raise ValueError(
                    f"AgentType {agent_type!r} 已注册到 "
                    f"{existing.__module__}.{existing.__name__}，"
                    f"无法重复注册到 {cls.__module__}.{cls.__name__}"
                )
            _builder_registry[agent_type] = cls
            logger.debug(
                "注册 Builder: %s -> %s.%s",
                agent_type, cls.__module__, cls.__name__,
            )
        return cls

    return decorator


def get_registered_builders() -> Dict[AgentType, Type[Any]]:
    """返回注册表副本。

    修改返回的字典不会影响全局注册表。
    """
    return dict(_builder_registry)


def clear_registry() -> None:
    """清空注册表。

    .. warning:: 仅用于测试隔离，生产代码不应调用。
    """
    _builder_registry.clear()
