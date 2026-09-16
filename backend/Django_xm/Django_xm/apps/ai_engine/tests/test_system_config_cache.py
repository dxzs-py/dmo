"""SystemConfig Redis 缓存迁移测试（dj-08）。

覆盖：
- set_value 后 get_value 拿到新值（写读一致）
- cache.clear() 后同步上下文 get_value 回源 DB 并重建缓存（assertNumQueries 验证）
- set_value(key, None) 删除后 get_value 返回 default 且缓存无脏数据
- 异步上下文（asyncio.run 内）get_value 缓存未命中 → warning 日志 + default，不回源 DB
- warmup 全量写缓存：模拟 Redis 重启（cache.clear）后 warmup 恢复，get_value 零查询命中

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_system_config_cache --settings=Django_xm.settings.test
"""

import asyncio
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.core.cache import cache
from django.test import TestCase

from Django_xm.apps.ai_engine.models import (
    SYSTEM_CONFIG_CACHE_TTL,
    SystemConfig,
    _cache_key,
    warmup_system_config_cache,
)

LOGGER_NAME = "Django_xm.apps.ai_engine.models"


class SystemConfigCacheTests(TestCase):
    """dj-08：SystemConfig 缓存全量迁 django cache（LocMemCache 模拟同进程一致性）。"""

    def setUp(self):
        cache.clear()
        SystemConfig.objects.all().delete()

    def test_set_value_then_get_value_returns_new_value(self):
        """① set_value 后 get_value 拿到新值。"""
        payload = {"provider_id": "openai", "model_name": "gpt-4o-mini"}
        SystemConfig.set_value("default_chat_model", payload)
        self.assertEqual(SystemConfig.get_value("default_chat_model"), payload)

    def test_cache_miss_sync_rebuilds_from_db(self):
        """② cache.clear() 后同步上下文回源 DB 一次并重建缓存，二次读取零查询。"""
        payload = {"provider_id": "openai", "model_name": "gpt-4o-mini"}
        SystemConfig.set_value("default_chat_model", payload)
        cache.clear()
        with self.assertNumQueries(1):
            self.assertEqual(SystemConfig.get_value("default_chat_model"), payload)
        # 缓存已回写：再次读取不触达 DB，且 cache 中有值
        with self.assertNumQueries(0):
            self.assertEqual(SystemConfig.get_value("default_chat_model"), payload)
        self.assertEqual(cache.get(_cache_key("default_chat_model")), payload)

    def test_set_value_none_deletes_record_and_cache(self):
        """③ set_value(key, None) 删除后 get_value 返回 default 且缓存无脏数据。"""
        SystemConfig.set_value("default_chat_model", {"provider_id": "openai"})
        result = SystemConfig.set_value("default_chat_model", None)
        self.assertIsNone(result)
        self.assertFalse(SystemConfig.objects.filter(key="default_chat_model").exists())
        self.assertEqual(SystemConfig.get_value("default_chat_model", default={"fallback": 1}), {"fallback": 1})
        self.assertIsNone(cache.get(_cache_key("default_chat_model")))

    def test_async_context_returns_default_with_warning(self):
        """④ 异步上下文缓存未命中 → default + warning 日志，不回源 DB。"""
        SystemConfig.set_value("default_chat_model", {"provider_id": "openai"})
        cache.clear()  # 前置：清缓存确保未命中

        async def read_in_async():
            return SystemConfig.get_value("default_chat_model", default={"async_default": 1})

        with self.assertNumQueries(0), self.assertLogs(LOGGER_NAME, level="WARNING") as logs:
            value = asyncio.run(read_in_async())
        self.assertEqual(value, {"async_default": 1})
        self.assertTrue(any("default_chat_model" in message for message in logs.output))

    def test_warmup_restores_cache_after_clear(self):
        """⑤ 模拟 Redis 重启（cache.clear）后 warmup 全量恢复，get_value 零查询命中缓存。"""
        payload = {"provider_id": "openai", "model_name": "gpt-4o-mini"}
        SystemConfig.set_value("default_chat_model", payload)
        SystemConfig.set_value("helper_model", {"provider_id": "deepseek"})
        cache.clear()
        warmup_system_config_cache()
        self.assertEqual(cache.get(_cache_key("default_chat_model")), payload)
        self.assertEqual(cache.get(_cache_key("helper_model")), {"provider_id": "deepseek"})
        with self.assertNumQueries(0):
            self.assertEqual(SystemConfig.get_value("default_chat_model"), payload)
            self.assertEqual(SystemConfig.get_value("helper_model"), {"provider_id": "deepseek"})

    def test_get_missing_key_returns_default_without_caching(self):
        """补充：不存在的 key 返回 default 且不写缓存（无负缓存）。"""
        default = {"none": True}
        with self.assertNumQueries(1):
            self.assertEqual(SystemConfig.get_value("no_such_key", default=default), default)
        self.assertIsNone(cache.get(_cache_key("no_such_key")))

    def test_warmup_is_idempotent(self):
        """补充：warmup 幂等，重复调用结果一致。"""
        payload = {"provider_id": "openai"}
        SystemConfig.set_value("default_chat_model", payload)
        warmup_system_config_cache()
        warmup_system_config_cache()
        self.assertEqual(cache.get(_cache_key("default_chat_model")), payload)
        self.assertEqual(SYSTEM_CONFIG_CACHE_TTL, 3600)
