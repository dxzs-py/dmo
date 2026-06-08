from __future__ import annotations

import logging
from typing import Any, Dict

from Django_xm.apps.ai_engine.config import settings

logger = logging.getLogger(__name__)


def _build_common_agent_kwargs(config, agent_kwargs: Dict[str, Any]) -> Dict[str, Any]:
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