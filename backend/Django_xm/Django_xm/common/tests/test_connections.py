"""common/connections.py 单元测试（ARCH-09 Redis/env 配置通道收敛）。

覆盖：
- Redis URL/密码解析优先级：Django settings 属性（base.py 已做 env>Pydantic 合并）
  > 同名环境变量 > 兜底默认值（与 Pydantic 真相源默认值严格一致）
- get_redis_config 密码空串归一化为 None（redis-py 直连语义）
- 信令 URL 回退链（CELERY_BROKER_URL 属性 > env > 默认）与 signal_bus 同源委托
- PG 连接串三级优先级：env CHECKPOINTER_POSTGRES_URI > uri_override > DB_* env 拼接

运行（backend/Django_xm 目录，独立测试库避免并行代理互删）：
    $env:DB_NAME='p1_b2'; python manage.py test Django_xm.common.tests.test_connections
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase, override_settings

from Django_xm.common import connections


class _SettingsStub:
    """无任何属性的 settings 替身：模拟未配置 DJANGO_SETTINGS_MODULE 的独立进程。"""


class RedisUrlResolutionTests(SimpleTestCase):
    """get_redis_url / get_redis_password 解析链。"""

    def test_django_setting_takes_precedence_over_env(self):
        """Django settings 属性优先于环境变量（base.py 已完成 env 合并）。"""
        with (
            override_settings(REDIS_URL="redis://cfg-host:6379/9"),
            mock.patch.dict(os.environ, {"REDIS_URL": "redis://env-host:6379/8"}),
        ):
            self.assertEqual(connections.get_redis_url(), "redis://cfg-host:6379/9")

    def test_falls_back_to_env_without_django_setting(self):
        """settings 属性缺失时回退同名环境变量。"""
        with (
            mock.patch("django.conf.settings", _SettingsStub()),
            mock.patch.dict(os.environ, {"REDIS_URL": "redis://env-host:6379/8"}),
        ):
            self.assertEqual(connections.get_redis_url(), "redis://env-host:6379/8")

    def test_falls_back_to_default_without_env(self):
        """settings 与 env 均缺失时回退兜底默认值。"""
        with (
            mock.patch("django.conf.settings", _SettingsStub()),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(connections.get_redis_url(), connections.DEFAULT_REDIS_URL)

    def test_password_returns_raw_value_for_django_cache(self):
        """get_redis_password 保留原始值（django_redis 接受空字符串）。"""
        with override_settings(REDIS_PASSWORD="secret"):
            self.assertEqual(connections.get_redis_password(), "secret")
        with override_settings(REDIS_PASSWORD=""):
            self.assertEqual(connections.get_redis_password(), "")


class RedisConfigTests(SimpleTestCase):
    """get_redis_config：直连 redis-py 客户端语义。"""

    def test_config_password_normalized_to_none_when_empty(self):
        """空字符串密码归一化为 None（与两个历史直连消费方行为一致）。"""
        with (
            override_settings(REDIS_URL="redis://h:6379/1", REDIS_PASSWORD=""),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(
                connections.get_redis_config(),
                {"url": "redis://h:6379/1", "password": None},
            )

    def test_config_password_kept_when_set(self):
        with override_settings(REDIS_PASSWORD="secret"):
            config = connections.get_redis_config()
        self.assertEqual(config["password"], "secret")


class SignalRedisUrlTests(SimpleTestCase):
    """get_signal_redis_url：Celery broker 连接串复用与回退链。"""

    def test_uses_django_setting(self):
        """优先读 Django settings 属性（发布/订阅同串，Pub/Sub 按 DB 隔离）。"""
        with override_settings(CELERY_BROKER_URL="redis://cfg-broker:6379/3"):
            self.assertEqual(connections.get_signal_redis_url(), "redis://cfg-broker:6379/3")

    def test_env_fallback_without_django_setting(self):
        with (
            mock.patch("django.conf.settings", _SettingsStub()),
            mock.patch.dict(os.environ, {"CELERY_BROKER_URL": "redis://env-broker:6379/5"}),
        ):
            self.assertEqual(connections.get_signal_redis_url(), "redis://env-broker:6379/5")

    def test_default_fallback(self):
        """回退默认值与 Pydantic celery_broker_url 默认值一致（行为保持）。"""
        with (
            mock.patch("django.conf.settings", _SettingsStub()),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(
                connections.get_signal_redis_url(),
                connections.DEFAULT_SIGNAL_REDIS_URL,
            )


class PostgresConnectionStringTests(SimpleTestCase):
    """get_postgres_connection_string：三级优先级与拼接默认值。"""

    def test_env_uri_wins_over_override(self):
        """env 完整连接串优先级最高（与 checkpointer_factory 历史顺序一致）。"""
        env = {"CHECKPOINTER_POSTGRES_URI": "postgresql://u:p@envhost:5433/envdb"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                connections.get_postgres_connection_string(uri_override="postgresql://x:y@o:1/o"),
                "postgresql://u:p@envhost:5433/envdb",
            )

    def test_override_used_without_env_uri(self):
        """无 env 连接串时使用调用方透传的配置覆盖值。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                connections.get_postgres_connection_string(uri_override="postgresql://x:y@o:1/o"),
                "postgresql://x:y@o:1/o",
            )

    def test_assembled_from_db_env_vars(self):
        """回退拼接：DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD 环境变量。"""
        env = {
            "DB_HOST": "pg.internal",
            "DB_PORT": "6543",
            "DB_NAME": "xm",
            "DB_USER": "alice",
            "DB_PASSWORD": "s3cret",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                connections.get_postgres_connection_string(),
                "postgresql://alice:s3cret@pg.internal:6543/xm",
            )

    def test_defaults_without_env(self):
        """无任何 env 时使用与 Pydantic 真相源一致的默认值。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                connections.get_postgres_connection_string(),
                "postgresql://postgres:@127.0.0.1:5432/langchain_xm",
            )


class ConsumerDelegationTests(SimpleTestCase):
    """消费方委托一致性：消费者与 connections 同源。

    注：RedisDirectClient 的委托测试已迁移至
    apps/cache_manager/tests/test_redis_delegation.py
    （common 基础层禁止依赖 apps 业务层契约）。
    """

    def test_signal_bus_delegates_to_connections(self):
        """signal_bus.get_signal_redis_url 保持原 API 且与 connections 同源。"""
        from Django_xm.common.signal_bus import get_signal_redis_url as bus_url

        with override_settings(CELERY_BROKER_URL="redis://same:6379/3"):
            self.assertEqual(bus_url(), connections.get_signal_redis_url())
