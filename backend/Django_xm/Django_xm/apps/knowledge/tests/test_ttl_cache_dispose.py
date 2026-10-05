"""C3（KV-3）TTL 缓存逐出释放 engine 连接池回归测试。

契约（remediate-rag-ai-correctness C3）：
1. _TTLCache 的 TTL 过期 / LRU 逐出 / remove 路径触发 on_evict 回调
   （此前直接丢弃 value → PGVector engine 连接池泄漏，约 480 连接/进程上限）；
2. clear() 不触发回调——快照释放由 IndexManager.clear_cache() 调用方负责；
3. IndexManager._cache 已挂 dispose 回调；
4. 自愈路径消费 clear_cache 快照（不再丢弃）。

运行方式：
   python manage.py test Django_xm.apps.knowledge.tests.test_ttl_cache_dispose --noinput
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from Django_xm.apps.knowledge.services.index_service import IndexManager, _TTLCache
from Django_xm.apps.knowledge.vector_store import dispose_vector_stores


def _fake_store():
    return SimpleNamespace(_engine=MagicMock())


class TTLCacheEvictTests(SimpleTestCase):
    def _make_cache(self, **kwargs):
        on_evict = MagicMock()
        return _TTLCache(on_evict=on_evict, **kwargs), on_evict

    def test_lru_evict_triggers_callback(self):
        cache, on_evict = self._make_cache(maxsize=2)
        cache.set("a", "va")
        cache.set("b", "vb")
        cache.set("c", "vc")  # 逐出最旧的 a
        on_evict.assert_called_once_with("a", "va")

    def test_ttl_expire_triggers_callback(self):
        cache, on_evict = self._make_cache(ttl=0.01)
        cache.set("a", "va")
        time.sleep(0.05)
        self.assertIsNone(cache.get("a"))  # 过期路径
        on_evict.assert_called_once_with("a", "va")

    def test_remove_triggers_callback(self):
        cache, on_evict = self._make_cache()
        cache.set("a", "va")
        cache.remove("a")
        on_evict.assert_called_once_with("a", "va")
        cache.remove("a")  # 再移除：不存在，不回调
        on_evict.assert_called_once()

    def test_clear_does_not_trigger_callback(self):
        """clear() 不回调——快照由 clear_cache() 调用方负责释放。"""
        cache, on_evict = self._make_cache()
        cache.set("a", "va")
        cache.clear()
        on_evict.assert_not_called()

    def test_callback_exception_does_not_break_cache(self):
        """回调抛异常不影响缓存自身操作。"""
        cache, on_evict = self._make_cache(maxsize=1)
        on_evict.side_effect = RuntimeError("dispose 失败")
        cache.set("a", "va")
        cache.set("b", "vb")  # 逐出 a → 回调抛异常 → 不应影响 set
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), "vb")


class IndexManagerEvictHookTests(SimpleTestCase):
    def test_index_manager_cache_has_evict_hook(self):
        self.assertIsNotNone(IndexManager._cache._on_evict)

    def test_evict_hook_disposes_engine(self):
        """真实回调链：逐出带 _engine 的 store → engine.dispose 被调用。"""
        store = _fake_store()
        IndexManager._cache.set("t-kv3", store)
        IndexManager._cache.remove("t-kv3")
        store._engine.dispose.assert_called_once()

    def test_dispose_vector_stores_helper(self):
        engine = MagicMock()
        broken = SimpleNamespace(_engine=None)
        no_engine = SimpleNamespace()
        disposed = dispose_vector_stores(
            [SimpleNamespace(_engine=engine), broken, no_engine]
        )
        self.assertEqual(disposed, 1)
        engine.dispose.assert_called_once()
