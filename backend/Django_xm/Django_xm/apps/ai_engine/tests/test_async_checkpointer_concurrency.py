"""Task 5（A16）行为锁定测试：get_async_checkpointer 同 loop 并发首访仅创建一次。

旧实现在锁外 await 创建：同 loop 多个协程同时 cache miss 会各自创建连接池，
后写覆盖前者，先创建的池变成 cm_ref 残留泄漏。修复后 per-cache_key 的
asyncio.Lock + 双检保证创建全程互斥。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_async_checkpointer_concurrency --noinput
"""

import asyncio
import os
from collections import OrderedDict
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

_FACTORY = "Django_xm.apps.ai_engine.services.checkpointer_factory"


class AsyncCheckpointerConcurrentFirstAccessTests(SimpleTestCase):
    """同 loop 并发首访：创建函数只被调用一次，所有协程拿到同一实例。"""

    def test_concurrent_first_access_creates_once(self):
        from Django_xm.apps.ai_engine.services import checkpointer_factory as factory

        created: list[object] = []

        async def fake_create_sqlite(db_path=None):
            # 制造并发窗口：让首个协程在创建期间挂起，其余协程走到锁竞争
            await asyncio.sleep(0.01)
            instance = SimpleNamespace(name="fake-async-sqlite")
            created.append(instance)
            return instance

        fresh_cache = OrderedDict()
        fresh_locks: dict = {}
        with (
            mock.patch.object(factory, "_checkpointer_cache", fresh_cache),
            mock.patch.object(factory, "_async_create_locks", fresh_locks),
            mock.patch.object(factory, "_create_async_sqlite_checkpointer", side_effect=fake_create_sqlite),
        ):
            async def _run():
                return await asyncio.gather(
                    *[factory.get_async_checkpointer(backend="sqlite", db_path=":memory:") for _ in range(10)]
                )

            results = asyncio.run(_run())

        self.assertEqual(len(created), 1, f"并发首访重复创建 {len(created)} 次（A16 竞态回归）")
        self.assertEqual(len(results), 10)
        for instance in results:
            self.assertIs(instance, created[0])

    def test_release_clears_create_lock(self):
        """release_async_checkpointer 同步清理 per-key 创建锁（防一次性 loop 累积）。"""
        from Django_xm.apps.ai_engine.services import checkpointer_factory as factory

        fresh_cache = OrderedDict()
        fresh_locks: dict = {}

        async def fake_create_sqlite(db_path=None):
            return SimpleNamespace(name="fake-async-sqlite")

        with (
            mock.patch.object(factory, "_checkpointer_cache", fresh_cache),
            mock.patch.object(factory, "_async_create_locks", fresh_locks),
            mock.patch.object(factory, "_create_async_sqlite_checkpointer", side_effect=fake_create_sqlite),
            mock.patch.object(factory, "_main_loop_id", None),
        ):
            async def _run():
                await factory.get_async_checkpointer(backend="sqlite", db_path=":memory:")
                keys = list(factory._async_create_locks.keys())
                await factory.release_async_checkpointer(backend="sqlite", db_path=":memory:")
                return keys

            created_keys = asyncio.run(_run())

        self.assertEqual(len(created_keys), 1, "创建后应恰好注册一把 per-key 锁")
        self.assertEqual(len(fresh_locks), 0, "释放后 per-key 创建锁未清理（内存泄漏回归）")
