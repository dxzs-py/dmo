"""异步 Redis 客户端工厂（按事件循环隔离，供 RedisCachedCheckpointer 复用）。

与 ``AsyncPostgresSaver`` 同理，``redis.asyncio`` 的连接池绑定创建时的事件循环，
跨事件循环复用会报 "bound to a different event loop"。因此按 ``loop_id`` 缓存，
与 ``checkpointer_factory.get_async_checkpointer`` 的缓存策略一致。

连接信息复用项目现有 Django cache 的 Redis 配置（``REDIS_URL`` / ``redis_url``），
懒加载复用连接池，不每次新建连接，避免连接泄漏。
"""

from __future__ import annotations

import asyncio
import os
import threading

import redis.asyncio as redis_async

from Django_xm.apps.ai_engine.config import settings
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

_client_cache: dict[int, redis_async.Redis] = {}
_lock = threading.Lock()


def get_async_redis_client() -> redis_async.Redis:
    """获取当前事件循环对应的异步 Redis 客户端（懒加载，按 loop_id 缓存）。

    Returns:
        异步 Redis 客户端实例；同一事件循环内复用同一连接池。
    """
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0

    with _lock:
        if loop_id in _client_cache:
            return _client_cache[loop_id]

        url = os.environ.get("REDIS_URL", settings.redis_url)
        password = os.environ.get("REDIS_PASSWORD", settings.redis_password) or None
        client = redis_async.Redis.from_url(
            url,
            password=password,
            socket_connect_timeout=3,
            socket_timeout=3,
            decode_responses=False,
        )
        _client_cache[loop_id] = client
        logger.debug(f"[RedisCachedCheckpointer] 创建异步 Redis 客户端: loop={loop_id}")
        return client


async def release_async_redis_client() -> None:
    """释放当前事件循环的异步 Redis 客户端连接池。

    在流式请求结束时调用（与 ``release_async_checkpointer`` 配套），
    关闭当前事件循环对应的 Redis 连接池，防止连接泄漏。
    """
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0

    with _lock:
        client = _client_cache.pop(loop_id, None)
    if client is not None:
        try:
            await client.aclose()
            logger.debug(f"[RedisCachedCheckpointer] 已释放异步 Redis 客户端: loop={loop_id}")
        except Exception as e:
            logger.debug(f"[RedisCachedCheckpointer] 释放异步 Redis 客户端失败 ({loop_id}): {e}")
