"""1.1 持久化后端装配测试（fix-review-round2：store_backend / checkpointer_backend）。

背景：checkpointer_factory 经 ``getattr(settings, "store_backend", "memory")`` /
``getattr(settings, "checkpointer_backend", "sqlite")`` 读 ai_engine pydantic 单例
（真相源，env 键 STORE_BACKEND/CHECKPOINTER_BACKEND，默认 postgres）；此前
settings/base.py 未装配同名 Django settings 通道属性（ARCH-05 装配模式对齐
ATTACHMENT_*），本任务补齐并由本测试钉住一致性。

覆盖：
1. Django settings 通道防漂移：settings.STORE_BACKEND / CHECKPOINTER_BACKEND
   与 ai_engine 单例字段一致；
2. get_store 分支选择（mock settings 断言）：postgres → PostgresStore 分支、
   memory → InMemoryStore 分支、未知后端 → 回退 memory（warning）；
3. 降级路径保留：langgraph-store-postgres 不可用 → InMemoryStore +
   _log_memory_store_fallback error 告警（生产 DEBUG=False 下）。

运行（backend/Django_xm 目录，conda env langchain_xm，测试库 DB_NAME=langchain_verify）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_store_backend_wiring
"""

from collections import OrderedDict
from unittest import mock

from django.conf import settings as django_settings
from django.test import SimpleTestCase

from Django_xm.apps.ai_engine import config as ai_config_module
from Django_xm.apps.ai_engine.services import checkpointer_factory

_FACTORY_LOGGER = "Django_xm.apps.ai_engine.services.checkpointer_factory"


class StoreBackendSettingsWiringTests(SimpleTestCase):
    """Django settings 通道装配防漂移断言（settings/base.py ↔ ai_engine 单例）。"""

    def test_store_backend_channel_matches_ai_config(self):
        self.assertEqual(django_settings.STORE_BACKEND, ai_config_module.settings.store_backend)

    def test_checkpointer_backend_channel_matches_ai_config(self):
        self.assertEqual(
            django_settings.CHECKPOINTER_BACKEND, ai_config_module.settings.checkpointer_backend
        )


class GetStoreBranchSelectionTests(SimpleTestCase):
    """get_store 分支选择：按 settings.store_backend（或显式 backend 参数）选择。"""

    def setUp(self):
        # 隔离模块级 store 缓存：分支选择断言不受其他用例缓存污染
        self._cache_patcher = mock.patch.object(checkpointer_factory, "_store_cache", OrderedDict())
        self._cache_patcher.start()
        self.addCleanup(self._cache_patcher.stop)

    def test_default_backend_reads_settings_singleton_postgres_branch(self):
        """无显式 backend 时读 ai_engine 单例 settings.store_backend（默认 postgres）"""
        self.assertEqual(ai_config_module.settings.store_backend, "postgres")
        with mock.patch.object(
            checkpointer_factory, "_create_postgres_store", return_value=object()
        ) as mock_pg, mock.patch.object(
            checkpointer_factory, "_create_memory_store", return_value=None
        ) as mock_mem:
            store = checkpointer_factory.get_store()
            self.assertIs(store, mock_pg.return_value)
            mock_pg.assert_called_once()
            mock_mem.assert_not_called()

    def test_settings_memory_selects_memory_branch(self):
        """settings.store_backend=memory → InMemoryStore 分支（不触碰 Postgres）"""
        with mock.patch.object(
            ai_config_module.settings, "store_backend", "memory"
        ), mock.patch.object(
            checkpointer_factory, "_create_postgres_store", return_value=object()
        ) as mock_pg, mock.patch.object(
            checkpointer_factory,
            "_create_memory_store",
            return_value=object(),
        ) as mock_mem:
            store = checkpointer_factory.get_store()
            self.assertIs(store, mock_mem.return_value)
            mock_pg.assert_not_called()
            mock_mem.assert_called_once()

    def test_unknown_backend_falls_back_to_memory(self):
        """未知后端 → warning + 回退 memory 分支"""
        with mock.patch.object(
            ai_config_module.settings, "store_backend", "mysql"
        ), self.assertLogs(_FACTORY_LOGGER, level="WARNING") as logs, mock.patch.object(
            checkpointer_factory,
            "_create_memory_store",
            return_value=object(),
        ) as mock_mem:
            store = checkpointer_factory.get_store()
            self.assertIs(store, mock_mem.return_value)
            mock_mem.assert_called_once()
        self.assertTrue(any("未知的 store 后端" in line for line in logs.output))


class PostgresStoreDegradationTests(SimpleTestCase):
    """真降级路径保留：langgraph-store-postgres 不可用 → InMemoryStore + error 告警。"""

    def test_missing_package_falls_back_to_memory_store_with_error_log(self):
        # sys.modules 置 None 使 `from langgraph.store.postgres import PostgresStore` 抛 ImportError
        with mock.patch.dict(
            "sys.modules", {"langgraph.store.postgres": None}
        ), self.assertLogs(_FACTORY_LOGGER, level="ERROR") as logs, mock.patch.object(
            django_settings, "DEBUG", False, create=True
        ):
            store = checkpointer_factory.get_store(backend="postgres")
            from langgraph.store.memory import InMemoryStore

            self.assertIsInstance(store, InMemoryStore)
        self.assertTrue(
            any("Store 回退到 InMemoryStore" in line for line in logs.output),
            f"降级 error 告警缺失: {logs.output}",
        )
