"""Checkpointer 工厂 loop 隔离与非 postgres 生命周期测试（fix-review-round3 2.7）。

历史缺陷：
1. cache_key 以 id(loop) 标识 loop——loop 关闭回收后 id 数值可能被新 loop
   复用，旧 cache_key 命中绑定已死 loop 的实例（"bound to a different
   event loop" / 已关闭连接）；
2. release_async_checkpointer 对非 postgres 后端直接 return——一次性 loop
   （子代理独立线程）的 sqlite 实例只进不出，堆积到 LRU 淘汰，而淘汰回调
   对绑定已死 loop 的实例无效（跨 loop __aexit__ 不可靠）→ 连接泄漏。

修复语义：
- loop token：弱引用稳定映射（同一 loop 恒定、回收后不复用）；
- 非 postgres 异步实例进 ephemeral 注册表（同 loop 共享），release 在同一
  loop 上关闭（用完即弃），死 loop 条目随 sweep 丢弃。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.ai_engine.tests.test_checkpointer_loop_isolation
"""

import asyncio
import gc
import os
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

_FACTORY = "Django_xm.apps.ai_engine.services.checkpointer_factory"


class LoopCacheTokenTests(SimpleTestCase):
    """_loop_cache_token：同 loop 恒定 / 回收后不复用（id 复用风险消除）。"""

    def test_same_loop_returns_same_token(self):
        from Django_xm.apps.ai_engine.services.checkpointer_factory import _loop_cache_token

        async def _run():
            loop = asyncio.get_running_loop()
            return _loop_cache_token(loop), _loop_cache_token(loop)

        token_a, token_b = asyncio.run(_run())
        self.assertEqual(token_a, token_b)
        self.assertNotEqual(token_a, "noloop")

    def test_none_loop_returns_fixed_token(self):
        from Django_xm.apps.ai_engine.services.checkpointer_factory import _loop_cache_token

        self.assertEqual(_loop_cache_token(None), "noloop")

    def test_sequential_loops_get_distinct_tokens(self):
        """先后两个 loop（前者已关闭回收）→ token 不同（即使 id 数值复用）。"""
        from Django_xm.apps.ai_engine.services.checkpointer_factory import _loop_cache_token

        async def _capture():
            return _loop_cache_token(asyncio.get_running_loop())

        # loop A：打开期间 token 稳定（同对象两次取值一致）
        loop_a = asyncio.new_event_loop()
        try:
            token_a1 = _loop_cache_token(loop_a)
            token_a2 = _loop_cache_token(loop_a)
        finally:
            loop_a.close()
            del loop_a
            gc.collect()

        # loop B（asyncio.run 新 loop）：即使 id(loop) 数值与 loop A 复用，
        # token 也必须不同（弱引用映射不复用旧 token）
        token_b = asyncio.run(_capture())

        self.assertEqual(token_a1, token_a2, "同一 loop 对象 token 必须稳定")
        self.assertNotEqual(token_a1, token_b, "先后两个 loop 的 token 必须不同（id 复用不得误命中）")


class EphemeralSqliteCheckpointerTests(SimpleTestCase):
    """非 postgres（sqlite）异步实例：同 loop 共享 / release 关闭 / 不进 LRU。"""

    def setUp(self):
        from Django_xm.apps.ai_engine.services import checkpointer_factory as factory

        self.factory = factory
        patches = [
            mock.patch.object(factory, "_checkpointer_cache", OrderedDict()),
            mock.patch.object(factory, "_ephemeral_checkpointers", {}),
            mock.patch.object(factory, "_async_create_locks", {}),
            mock.patch.object(factory, "_main_loop_id", None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _fake_sqlite(self, created: list):
        def _create(db_path=None):
            conn = SimpleNamespace(closed=False)

            async def _close():
                conn.closed = True

            instance = SimpleNamespace(name=f"fake-sqlite-{len(created)}", conn=conn, close=_close)
            created.append(instance)
            return instance

        return _create

    def test_same_loop_shares_instance_and_bypasses_lru(self):
        """同 loop 两次获取 → 同一实例；实例不进全局 LRU（ephemeral 归组）。"""
        factory = self.factory
        created: list = []
        with mock.patch.object(
            factory, "_create_async_sqlite_checkpointer", side_effect=self._fake_sqlite(created)
        ):
            async def _run():
                first = await factory.get_async_checkpointer(backend="sqlite", db_path=":memory:")
                second = await factory.get_async_checkpointer(backend="sqlite", db_path=":memory:")
                return first, second

            first, second = asyncio.run(_run())

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        self.assertEqual(len(factory._checkpointer_cache), 0, "非 postgres 实例不得进入全局 LRU")
        self.assertEqual(len(factory._ephemeral_checkpointers), 1)

    def test_release_closes_connection_and_next_loop_gets_new_instance(self):
        """release 在同一 loop 上关闭连接；新 loop 获取到全新实例。"""
        factory = self.factory
        created: list = []
        with mock.patch.object(
            factory, "_create_async_sqlite_checkpointer", side_effect=self._fake_sqlite(created)
        ):
            async def _get_and_release():
                cp = await factory.get_async_checkpointer(backend="sqlite", db_path=":memory:")
                await factory.release_async_checkpointer(backend="sqlite", db_path=":memory:")
                return cp

            cp_a = asyncio.run(_get_and_release())
            self.assertTrue(cp_a.conn.closed, "release 未关闭 sqlite 连接（泄漏回归）")
            self.assertEqual(len(factory._ephemeral_checkpointers), 0)

            cp_b = asyncio.run(_get_and_release())
            self.assertIsNot(cp_a, cp_b, "release 后同 loop token 应获取新实例")
            self.assertEqual(len(created), 2)

    def test_release_without_entry_is_noop(self):
        """无对应条目时 release 安全 no-op（幂等）。"""
        factory = self.factory
        asyncio.run(factory.release_async_checkpointer(backend="sqlite", db_path=":memory:"))

    def test_main_loop_sqlite_instance_not_released(self):
        """主 loop 上的非 postgres 实例常驻（多会话共享，release 不误关）。"""
        factory = self.factory
        created: list = []
        with mock.patch.object(
            factory, "_create_async_sqlite_checkpointer", side_effect=self._fake_sqlite(created)
        ):
            async def _run():
                loop = asyncio.get_running_loop()
                with mock.patch.object(factory, "_main_loop_id", id(loop)):
                    cp = await factory.get_async_checkpointer(backend="sqlite", db_path=":memory:")
                    await factory.release_async_checkpointer(backend="sqlite", db_path=":memory:")
                    return cp

            cp = asyncio.run(_run())

        self.assertFalse(cp.conn.closed, "主 loop 实例被 release 误关（问题L 回归）")
        self.assertEqual(len(factory._ephemeral_checkpointers), 1, "主 loop 条目应保留（常驻）")


if __name__ == "__main__":
    unittest.main()
