"""Redis 工具函数（5-2 收敛：同步 Redis 客户端获取唯一出口）。

- :func:`get_redis_client`：Django cache 框架优先（与 cache 框架共享连接池，
  decode 语义由 CACHES 配置决定），获取失败时回退直连客户端；
- :func:`get_direct_redis_client`：直连 redis-py 客户端（decode_responses=True、
  socket 超时 3s、进程级单例 + ping 重连）——原
  ``cache_manager/services/cache_service.py::RedisDirectClient.get_client``
  的实现细节收敛至此，供需要字符串语义/绕过 cache 框架的场景使用。

连接参数（URL/password）单一来源：``common/connections.get_redis_config()``。

异步专用路径保留于 ``apps/ai_engine/services/redis_async_client.py``
（真差异：``redis.asyncio`` 连接池绑定创建时的事件循环，跨 loop 复用会报
"bound to a different event loop"，按 loop_id 隔离缓存，不可与同步路径合并）。
"""

import logging

logger = logging.getLogger(__name__)


class _DirectRedisClientHolder:
    """直连客户端进程级单例持有者（ping 失败自动重建）。"""

    _client = None

    @classmethod
    def get_client(cls):
        try:
            import redis

            from Django_xm.common.connections import get_redis_config

            config = get_redis_config()
            cls._client = redis.from_url(
                config["url"],
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                password=config["password"],
            )
            cls._client.ping()
        except Exception:
            logger.exception("Redis 客户端初始化失败")
            cls._client = None
        return cls._client


def get_direct_redis_client():
    """获取直连 redis-py 客户端（进程级单例 + ping 重连）。

    连接参数与原 ``RedisDirectClient.get_client`` 严格一致：
    ``decode_responses=True``、``socket_connect_timeout=3``、
    ``socket_timeout=3``、password 空串归一化为 ``None``；
    URL/密码解析统一走 ``common/connections.get_redis_config()``。

    Returns:
        redis.Redis 实例；初始化失败时返回 None（不抛出，由调用方判空）。
    """
    client = _DirectRedisClientHolder._client
    if client is not None:
        try:
            client.ping()
            return client
        except Exception:
            _DirectRedisClientHolder._client = None
    return _DirectRedisClientHolder.get_client()


def get_redis_client():
    """获取 Redis 客户端（同步获取唯一入口）。

    优先通过 Django cache 框架获取底层客户端；获取失败时回退直连客户端
    （:func:`get_direct_redis_client`）。所有需要直连 Redis 的模块应统一
    通过此函数获取客户端，而非各自重复实现获取逻辑。
    """
    try:
        from django.core.cache import caches

        default_cache = caches["default"]
        if hasattr(default_cache, "client"):
            client = default_cache.client
            if hasattr(client, "get_client"):
                return client.get_client()
            return client
    except Exception as e:
        logger.warning(f"通过 Django cache 获取 Redis 客户端失败: {e}")
    return get_direct_redis_client()
