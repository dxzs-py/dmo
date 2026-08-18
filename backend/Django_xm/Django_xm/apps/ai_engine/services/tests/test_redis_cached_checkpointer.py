"""RedisCachedCheckpointer 装饰器单元测试（Task 5 / Task 8）。

覆盖 spec 验收项：
1. Redis 写失败不影响 PG（aput 先写 inner 成功，Redis 写失败忽略）；
2. 缓存 miss 回源 inner（aget_tuple 未命中时回源 PG 并回填）；
3. 限流回源（同一 key 并发穿透时锁只允许一个回源，锁失败不等待直接回源）；
4. alist 透传（不读写 Redis，直接委托 inner）；
5. 反序列化失败降级（缓存数据损坏时丢弃缓存、回源 inner）；
6. Redis 宕机自动降级（Redis 操作异常时回退 inner，不抛错中断）。

运行：
    python -m unittest Django_xm.apps.ai_engine.services.tests.test_redis_cached_checkpointer
"""

import unittest
from unittest import mock

from langgraph.checkpoint.base import CheckpointTuple

from Django_xm.apps.ai_engine.services.redis_cached_checkpointer import (
    RedisCachedCheckpointer,
)

_CFG = {"configurable": {"thread_id": "t1", "checkpoint_id": "c1"}}
_PARENT_CFG = {"configurable": {"thread_id": "t1", "checkpoint_id": "c0"}}


def _make_tuple(config=_CFG, parent_config=None, cid="c1"):
    return CheckpointTuple(
        config=config,
        checkpoint={
            "id": cid,
            "v": 1,
            "ts": "2026-01-01T00:00:00Z",
            "channel_values": {"messages": []},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        },
        metadata={"step": 0, "source": "input"},
        parent_config=parent_config,
        pending_writes=[],
    )


class _FakeRedis:
    """内存版异步 Redis 客户端（模拟 get/set/delete/set-NX）。"""

    def __init__(self, fail: bool = False):
        self._store: dict[str, bytes] = {}
        self._meta: dict[str, dict] = {}
        self._fail = fail

    async def get(self, key):
        if self._fail:
            raise ConnectionError("redis down")
        return self._store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if self._fail:
            raise ConnectionError("redis down")
        if nx and key in self._store:
            return False
        self._store[key] = value
        self._meta[key] = {"ex": ex}
        return True


class _FakeInner:
    """记录调用行为的 fake AsyncPostgresSaver。"""

    def __init__(self):
        self.store: dict[str, CheckpointTuple] = {}
        self.aget_calls = 0
        self.aput_calls = 0
        self.alist_calls = 0
        self.adelete_calls = 0

    async def aget_tuple(self, config):
        self.aget_calls += 1
        return self.store.get(config["configurable"]["checkpoint_id"])

    async def aput(self, config, checkpoint, metadata, new_versions):
        self.aput_calls += 1
        self.store["c1"] = _make_tuple(parent_config=_PARENT_CFG)
        return _CFG

    async def alist(self, config, *, filter=None, before=None, limit=None):
        self.alist_calls += 1
        for item in [self.store.get("c1")]:
            if item is not None:
                yield item

    async def adelete_thread(self, thread_id):
        self.adelete_calls += 1
        self.store.clear()


class TestRedisCachedCheckpointer(unittest.IsolatedAsyncioTestCase):
    def _make(self, inner=None, redis=None, ttl=1800, lock_ttl=5):
        inner = inner or _FakeInner()
        redis = redis or _FakeRedis()
        return RedisCachedCheckpointer(inner=inner, redis_client=redis, ttl=ttl, lock_ttl=lock_ttl), inner, redis

    async def test_aput_writes_pg_then_redis(self):
        cp, inner, redis = self._make()
        checkpoint = _make_tuple().checkpoint
        result = await cp.aput(_PARENT_CFG, checkpoint, {"step": 1}, {})
        self.assertEqual(inner.aput_calls, 1, "aput 必须先写 inner（PG）")
        self.assertEqual(result, _CFG)
        self.assertIn(cp._cache_key("t1", "c1"), redis._store, "PG 写成功后才写 Redis")

    async def test_aput_redis_write_failure_does_not_break(self):
        cp, inner, _redis = self._make(redis=_FakeRedis(fail=True))
        checkpoint = _make_tuple().checkpoint
        result = await cp.aput(_PARENT_CFG, checkpoint, {"step": 1}, {})
        self.assertEqual(result, _CFG, "Redis 写失败不影响 PG 结果")
        self.assertEqual(inner.aput_calls, 1)

    async def test_aget_tuple_cache_hit_skips_inner(self):
        cp, inner, _redis = self._make()
        await cp.aput(_PARENT_CFG, _make_tuple().checkpoint, {"step": 1}, {})
        inner.aget_calls = 0
        t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 0, "缓存命中不回源 inner")
        self.assertIsNotNone(t)
        self.assertEqual(t.checkpoint["id"], "c1")

    async def test_aget_tuple_miss_backfills(self):
        cp, inner, redis = self._make()
        inner.store["c1"] = _make_tuple()
        t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 1, "miss 回源 inner")
        self.assertIsNotNone(t)
        self.assertIn(cp._cache_key("t1", "c1"), redis._store, "回源成功后回填缓存")

    async def test_aget_tuple_redis_down_falls_back_to_pg(self):
        cp, inner, _redis = self._make(redis=_FakeRedis(fail=True))
        inner.store["c1"] = _make_tuple()
        t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 1, "Redis 宕机回源 inner")
        self.assertIsNotNone(t, "Redis 宕机不中断，返回 PG 结果")

    async def test_aget_tuple_lock_failure_direct_fallback(self):
        # 锁不可用（Redis 抛错）→ 不阻塞等待，直接回源 inner
        cp, inner, _ = self._make(redis=_FakeRedis(fail=True))
        inner.store["c1"] = _make_tuple()
        with mock.patch.object(cp, "_try_acquire_lock", new_callable=mock.AsyncMock, return_value=False):
            t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 1, "锁失败直接回源 inner，不阻塞")
        self.assertIsNotNone(t)

    async def test_alist_passthrough_no_redis(self):
        cp, inner, redis = self._make()
        inner.store["c1"] = _make_tuple()
        items = [item async for item in cp.alist(_CFG)]
        self.assertEqual(inner.alist_calls, 1, "alist 透传 inner")
        self.assertEqual(len(items), 1)
        self.assertEqual(redis._store, {}, "alist 不写 Redis")

    async def test_deserialize_failure_drops_cache_and_falls_back(self):
        cp, inner, redis = self._make()
        # 直接写入损坏缓存（无法反序列化）
        redis._store[cp._cache_key("t1", "c1")] = b"corrupted-not-json"
        inner.store["c1"] = _make_tuple()
        t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 1, "反序列化失败丢弃缓存回源 inner")
        self.assertIsNotNone(t)

    async def test_adelete_thread_only_inner(self):
        cp, inner, redis = self._make()
        await cp.aput(_PARENT_CFG, _make_tuple().checkpoint, {"step": 1}, {})
        await cp.adelete_thread("t1")
        self.assertEqual(inner.adelete_calls, 1)
        self.assertEqual(inner.store, {}, "inner PG 已删除")
        self.assertIn(cp._cache_key("t1", "c1"), redis._store, "Redis 不主动删（TTL 兜底）")

    async def test_lock_ttl_applied(self):
        cp, inner, redis = self._make(lock_ttl=7)
        inner.store["c1"] = _make_tuple()
        await cp.aget_tuple(_CFG)
        lock_key = cp._lock_key("t1", "c1")
        self.assertIn(lock_key, redis._store, "回源时设置限流锁")
        self.assertEqual(redis._meta[lock_key]["ex"], 7, "锁带配置的 TTL")

    async def test_lock_conflict_second_reader_does_not_backfill(self):
        # 限流回源：第一个 reader 抢到锁回源；第二个 reader（锁已被占）不等待直接回源
        cp, inner, redis = self._make()
        inner.store["c1"] = _make_tuple()
        # 预占锁：模拟第一个 reader 已抢锁回源中
        redis._store[cp._lock_key("t1", "c1")] = b"1"
        with mock.patch.object(cp, "_try_acquire_lock", new_callable=mock.AsyncMock, return_value=False):
            t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 1, "锁失败仍回源 inner（不阻塞）")
        self.assertIsNotNone(t)
        self.assertNotIn(cp._cache_key("t1", "c1"), redis._store, "未抢到锁不回填缓存（防 stampede）")


if __name__ == "__main__":
    unittest.main()
