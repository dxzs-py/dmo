"""ResilientRedisChannelLayer 回归测试（跨进程密钥漂移故障）。

根因背景：channel layer 曾以 ``symmetric_encryption_keys=[SECRET_KEY]`` 对通道消息
做对称加密。Django(8000) 与 FastAPI(8001) 的 SECRET_KEY 不一致时，
``channels_redis.core.RedisChannelLayer.receive_single`` 直接 deserialize 抛
``cryptography.fernet.InvalidToken``（无任何兜底）→ consumer 协程被打断 →
WebSocket 以 1011 关闭且不触发 disconnect()，前端表现为“生成期间每秒断一次、
结束后才一次性回放”。

覆盖：
- 单条不可解码消息被丢弃，连接继续接收下一条（不再打断 consumer）
- Redis 连接类错误不被吞掉（真故障照常上抛）
- 配置守卫：默认通道层必须是弹性实现，且不得再启用 symmetric_encryption_keys

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.common.tests.test_channels_layer_resilient
"""

import asyncio
import os
from unittest import mock

os.environ["DJANGO_SETTINGS_MODULE"] = "Django_xm.settings.test"
import django

django.setup()

from channels_redis.core import RedisChannelLayer
from django.test import SimpleTestCase
from redis.exceptions import ConnectionError as RedisConnectionError

from Django_xm.common.channels_layer import ResilientRedisChannelLayer


def _make_layer() -> ResilientRedisChannelLayer:
    """构造不连接 Redis 的通道层实例（构造期不建连）。"""
    return ResilientRedisChannelLayer(hosts=[{"address": "redis://127.0.0.1:6379/0"}])


class ResilientChannelLayerTests(SimpleTestCase):
    """坏消息不应打断连接。"""

    def test_poison_message_dropped_and_next_message_returned(self):
        """解密/反序列化失败的消息被丢弃，随后返回下一条正常消息。"""
        layer = _make_layer()
        expected = {"type": "broadcast_event", "event": {"type": "stream_event"}}

        with mock.patch.object(
            RedisChannelLayer,
            "receive",
            side_effect=[ValueError("unpack(b) received extra data."), expected],
        ):
            result = asyncio.run(layer.receive("specific.test!0"))

        self.assertEqual(result, expected)

    def test_redis_connection_error_still_propagates(self):
        """Redis 连接错误必须上抛，不能被当作坏消息吞掉。"""
        layer = _make_layer()

        with mock.patch.object(
            RedisChannelLayer,
            "receive",
            side_effect=RedisConnectionError("Connection closed by server"),
        ), self.assertRaises(RedisConnectionError):
            asyncio.run(layer.receive("specific.test!0"))


class ChannelLayerConfigGuardTests(SimpleTestCase):
    """配置守卫：防止回退到“加密封装 + 无兜底”的旧实现。

    注意：测试环境 ``settings/test.py`` 覆盖为 InMemoryChannelLayer，
    因此守卫必须直接读 ``settings/base.py`` 的模块级配置（生产实际生效的那份）。
    """

    def setUp(self):
        from Django_xm.settings import base as base_settings

        self.default_layer = base_settings.CHANNEL_LAYERS["default"]

    def test_default_backend_is_resilient(self):
        self.assertEqual(
            self.default_layer["BACKEND"],
            "Django_xm.common.channels_layer.ResilientRedisChannelLayer",
        )

    def test_symmetric_encryption_keys_removed(self):
        """对称加密会把跨进程 SECRET_KEY 漂移放大成连接崩溃，禁止再启用。"""
        self.assertNotIn("symmetric_encryption_keys", self.default_layer["CONFIG"])
