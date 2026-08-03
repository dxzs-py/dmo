"""
Redis 工具函数

提供统一的 Redis 客户端获取入口，消除各模块中重复定义的 _get_redis_client。
"""

from django.core.cache import cache


def get_redis_client():
    """获取默认缓存底层的 Redis 客户端。

    所有需要直连 Redis 的模块应统一通过此函数获取客户端，
    而非各自重复定义 _get_redis_client。
    """
    return cache.client.get_client()
