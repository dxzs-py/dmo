"""RedisDirectClient 与 common.connections 的委托一致性测试。

原位于 common/tests/test_connections.py（ConsumerDelegationTests），
因 common 基础层禁止依赖 apps 业务层（importlinter 契约）迁移至此——
cache_manager→common 为合法依赖方向。

运行：
    python manage.py test Django_xm.apps.cache_manager.tests.test_redis_delegation
"""

import os
from unittest import mock

from django.test import SimpleTestCase, override_settings

from Django_xm.common import connections


class RedisDirectClientDelegationTests(SimpleTestCase):
    """RedisDirectClient._get_redis_config 与 connections 同源。"""

    def test_redis_direct_client_config_delegates(self):
        """RedisDirectClient._get_redis_config 返回 (url, password) 且同源。"""
        from Django_xm.apps.cache_manager.services.cache_service import RedisDirectClient

        with (
            override_settings(REDIS_URL="redis://h:6379/1", REDIS_PASSWORD=""),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            url, password = RedisDirectClient._get_redis_config()
        self.assertEqual(url, "redis://h:6379/1")
        self.assertIsNone(password)
