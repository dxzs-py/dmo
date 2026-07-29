from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from Django_xm.apps.ai_engine.config import settings

logger = logging.getLogger(__name__)

# 默认 build 超时（秒），与 AgentConfig.build_timeout 默认值一致
DEFAULT_BUILD_TIMEOUT: float = 30.0


async def build_with_timeout(
    build_fn: Callable[[Any], Awaitable[Any]],
    config: Any,
    operation_name: str,
) -> Any:
    """包装 builder._build_internal 加入超时控制与错误日志。

    设计模板方法模式：
    - builder.build() 调用本函数，传入 self._build_internal 与 operation_name
    - 本函数从 config.build_timeout 读取超时（None 时用 DEFAULT_BUILD_TIMEOUT）
    - 超时后抛出 asyncio.TimeoutError，由调用方决定降级策略
    - 日志中包含 operation_name 便于排查（如 "DeepAgentBuilder.build"）

    Args:
        build_fn: builder._build_internal（真正的构建方法）
        config: AgentConfig 实例
        operation_name: 日志中的操作名（如 "BaseAgentBuilder.build"）

    Returns:
        build_fn 的返回值（agent / graph / adapter）

    Raises:
        asyncio.TimeoutError: 构建超时
        Exception: build_fn 抛出的其他异常透传
    """
    timeout: float | None = getattr(config, "build_timeout", None)
    if timeout is None:
        timeout = DEFAULT_BUILD_TIMEOUT
    try:
        return await asyncio.wait_for(build_fn(config), timeout=timeout)
    except TimeoutError:
        logger.exception(
            "%s 超时 (timeout=%ss)",
            operation_name,
            timeout,
        )
        raise
    except Exception:
        logger.exception("%s 失败", operation_name)
        raise


def _build_common_agent_kwargs(config, agent_kwargs: dict[str, Any]) -> dict[str, Any]:
    if config.checkpointer:
        agent_kwargs["checkpointer"] = config.checkpointer

    if config.store:
        agent_kwargs["store"] = config.store

    if config.context_schema:
        agent_kwargs["context_schema"] = config.context_schema

    if config.response_format:
        agent_kwargs["response_format"] = config.response_format

    if config.cache:
        agent_kwargs["cache"] = config.cache
    elif getattr(settings, "agent_cache_enabled", False):
        try:
            from langgraph.cache.memory import InMemoryCache

            agent_kwargs["cache"] = InMemoryCache()
            logger.info("自动注入 InMemoryCache（Agent 级别缓存）")
        except ImportError:
            logger.warning("langgraph.cache.memory.InMemoryCache 不可用")

    if config.debug:
        agent_kwargs["debug"] = config.debug

    if config.name:
        agent_kwargs["name"] = config.name

    if config.state_schema:
        agent_kwargs["state_schema"] = config.state_schema

    if config.interrupt_before:
        agent_kwargs["interrupt_before"] = config.interrupt_before

    if config.interrupt_after:
        agent_kwargs["interrupt_after"] = config.interrupt_after

    return agent_kwargs
