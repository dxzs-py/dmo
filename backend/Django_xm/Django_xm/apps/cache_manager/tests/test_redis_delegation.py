"""Redis 直连客户端与 common.connections / common.redis_utils 的委托一致性测试。

原位于 common/tests/test_connections.py（ConsumerDelegationTests），
因 common 基础层禁止依赖 apps 业务层（importlinter 契约）迁移至此——
cache_manager→common 为合法依赖方向。

5-2 收敛后：RedisDirectClient.get_client 委托
common.redis_utils.get_direct_redis_client（同步直连唯一出口），
连接参数经 common.connections.get_redis_config 解析。

运行：
    python manage.py test Django_xm.apps.cache_manager.tests.test_redis_delegation
"""

import os
from unittest import mock

from django.test import SimpleTestCase, override_settings

from Django_xm.common import connections, redis_utils


class FakeRedisClient:
    """最小 ping 协议桩（构造参数由测试捕获断言）。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def ping(self):
        return True


class RedisDirectClientDelegationTests(SimpleTestCase):
    """同步直连客户端获取的委托链与连接参数同源断言。"""

    def setUp(self):
        # 隔离 redis_utils 直连单例，避免跨测试污染
        self._saved_client = redis_utils._DirectRedisClientHolder._client
        redis_utils._DirectRedisClientHolder._client = None

    def tearDown(self):
        redis_utils._DirectRedisClientHolder._client = self._saved_client

    def test_direct_client_config_delegates_to_connections(self):
        """get_direct_redis_client 经 connections.get_redis_config 解析连接参数。"""
        with (
            override_settings(REDIS_URL="redis://h:6379/1", REDIS_PASSWORD=""),
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("redis.from_url", return_value=FakeRedisClient()) as mock_from_url,
        ):
            client = redis_utils.get_direct_redis_client()

        self.assertIsInstance(client, FakeRedisClient)
        kwargs = mock_from_url.call_args.kwargs
        self.assertEqual(mock_from_url.call_args.args[0], "redis://h:6379/1")
        self.assertIsNone(kwargs["password"])  # 空串密码归一化为 None（redis-py 语义）
        self.assertTrue(kwargs["decode_responses"])  # 直连客户端字符串语义（历史行为保持）
        self.assertEqual(kwargs["socket_connect_timeout"], 3)
        self.assertEqual(kwargs["socket_timeout"], 3)

    def test_redis_direct_client_delegates_to_redis_utils(self):
        """RedisDirectClient.get_client 委托 common.redis_utils 唯一出口。"""
        from Django_xm.apps.cache_manager.services import cache_service

        sentinel = object()
        # cache_service 顶部已按名导入 get_direct_redis_client，需 patch 其命名空间内引用
        with mock.patch.object(cache_service, "get_direct_redis_client", return_value=sentinel) as m:
            self.assertIs(cache_service.RedisDirectClient.get_client(), sentinel)
        m.assert_called_once_with()

    def test_connections_get_redis_config_shape(self):
        """get_redis_config 返回 (url, password) 形状且与 getter 同源。"""
        with (
            override_settings(REDIS_URL="redis://h:6379/1", REDIS_PASSWORD=""),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            config = connections.get_redis_config()
            self.assertEqual(config["url"], connections.get_redis_url())
        self.assertIsNone(config["password"])
