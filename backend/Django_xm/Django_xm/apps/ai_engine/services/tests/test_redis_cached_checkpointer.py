"""RedisCachedCheckpointer 装饰器单元测试（Task 5 / Task 8）。

覆盖 spec 验收项：
1. Redis 写失败不影响 PG（aput 先写 inner 成功，Redis 写失败忽略）；
2. 缓存 miss 回源 inner（aget_tuple 未命中时回源 PG 并回填）；
3. 限流回源（同一 key 并发穿透时锁只允许一个回源，锁失败不等待直接回源）；
4. alist 透传（不读写 Redis，直接委托 inner）；
5. 反序列化失败降级（缓存数据损坏时丢弃缓存、回源 inner）；
6. Redis 宕机自动降级（Redis 操作异常时回退 inner，不抛错中断）；
7. adelete_thread 主动清理 Redis（E1f：PG 删除后 scan_iter 扫描并批量删除
   该 thread 的缓存/锁键，跨 thread 隔离；无键不调 delete；Redis 失败仅
   warning 不中断删除流程）；
8. aput_writes 写后失效缓存（E3/N-5：aput 缓存 pending_writes=[]，put_writes
   族落库后若不失效，显式按 checkpoint_id 的读会拿到过期的空 pending_writes；
   失效后 miss 回源 PG 重建真值；Redis 失效失败不中断写路径；config 无
   checkpoint_id 时跳过失效；同步 put_writes 委托 inner=死路径无不一致面）。

运行：
    python -m unittest Django_xm.apps.ai_engine.services.tests.test_redis_cached_checkpointer
"""

import fnmatch
import unittest
from unittest import mock

from langgraph.checkpoint.base import CheckpointTuple

from Django_xm.apps.ai_engine.services.redis_cached_checkpointer import (
    CACHE_KEY_PREFIX,
    LOCK_KEY_PREFIX,
    RedisCachedCheckpointer,
)
from Django_xm.common.testing import FakeRedis, create_fake_redis

_CFG = {"configurable": {"thread_id": "t1", "checkpoint_id": "c1"}}
_PARENT_CFG = {"configurable": {"thread_id": "t1", "checkpoint_id": "c0"}}


def _make_tuple(config=_CFG, parent_config=None, cid="c1", pending=None):
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
        pending_writes=pending if pending is not None else [],
    )


class _ScanFakeRedis(FakeRedis):
    """带 scan_iter 的 FakeRedis 扩展（E1f）：记录 scan/delete 调用供断言。

    - scan_iter(match)：fnmatch 过滤 _store 键；fail=True 时抛 ConnectionError
      模拟 Redis 宕机（与父类 get/set/delete 行为一致）；
    - delete(*keys)：redis-py 多键签名，记录每次调用实参并逐键移除。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scan_matches: list = []
        self.deleted_calls: list[tuple] = []

    async def scan_iter(self, match=None, count=None):
        if self._fail:
            raise ConnectionError("redis down")
        self.scan_matches.append(match)
        for key in list(self._store):
            if match is None or fnmatch.fnmatchcase(key, match):
                yield key

    async def delete(self, *keys):
        if self._fail:
            raise ConnectionError("redis down")
        self.deleted_calls.append(keys)
        return sum(1 for key in keys if self._store.pop(key, None) is not None)


class _FakeInner:
    """记录调用行为的 fake AsyncPostgresSaver。"""

    def __init__(self):
        self.store: dict[str, CheckpointTuple] = {}
        self.aget_calls = 0
        self.aput_calls = 0
        self.aput_writes_calls = 0
        self.put_writes_calls = 0
        self.alist_calls = 0
        self.adelete_calls = 0

    async def aget_tuple(self, config):
        self.aget_calls += 1
        return self.store.get(config["configurable"]["checkpoint_id"])

    async def aput(self, config, checkpoint, metadata, new_versions):
        self.aput_calls += 1
        self.store["c1"] = _make_tuple(parent_config=_PARENT_CFG)
        return _CFG

    async def aput_writes(self, config, writes, task_id, task_path=""):
        self.aput_writes_calls += 1

    def put_writes(self, config, writes, task_id, task_path=""):
        self.put_writes_calls += 1

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
        redis = redis or create_fake_redis()
        return RedisCachedCheckpointer(inner=inner, redis_client=redis, ttl=ttl, lock_ttl=lock_ttl), inner, redis

    async def test_aput_writes_pg_then_redis(self):
        cp, inner, redis = self._make()
        checkpoint = _make_tuple().checkpoint
        result = await cp.aput(_PARENT_CFG, checkpoint, {"step": 1}, {})
        self.assertEqual(inner.aput_calls, 1, "aput 必须先写 inner（PG）")
        self.assertEqual(result, _CFG)
        self.assertIn(cp._cache_key("t1", "c1"), redis._store, "PG 写成功后才写 Redis")

    async def test_aput_redis_write_failure_does_not_break(self):
        cp, inner, _redis = self._make(redis=create_fake_redis(fail=True))
        checkpoint = _make_tuple().checkpoint
        result = await cp.aput(_PARENT_CFG, checkpoint, {"step": 1}, {})
        self.assertEqual(result, _CFG, "Redis 写失败不影响 PG 结果")
        self.assertEqual(inner.aput_calls, 1)

    async def test_aput_writes_invalidates_cache_and_reread_serves_pg_truth(self):
        # E3/N-5：aput 缓存 pending_writes=[]（当时属真）→ aput_writes 落库后
        # 必须失效对应缓存键，否则显式按 checkpoint_id 的读到过期空 pending_writes
        cp, inner, redis = self._make()
        await cp.aput(_PARENT_CFG, _make_tuple().checkpoint, {"step": 1}, {})
        self.assertIn(cp._cache_key("t1", "c1"), redis._store)
        # 模拟 PG 侧 pending writes 已落库（interrupt 场景：写入与 checkpoint 同 id）
        pending = [("__interrupt__", {"idx": 1, "value": "resume-me"})]
        inner.store["c1"] = _make_tuple(pending=pending)
        await cp.aput_writes(_CFG, pending, "task-1")
        self.assertEqual(inner.aput_writes_calls, 1, "aput_writes 先写 inner（PG）")
        self.assertNotIn(cp._cache_key("t1", "c1"), redis._store, "写后必须失效缓存键")
        # 失效后显式按 checkpoint_id 读：miss 回源 PG 拿真值并回填
        t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 1, "缓存已失效必须回源 inner")
        self.assertEqual(t.pending_writes, pending, "读到 PG 真值而非过期 []")
        self.assertIn(cp._cache_key("t1", "c1"), redis._store, "回源后回填缓存")

    async def test_aput_writes_without_invalidation_would_serve_stale_empty(self):
        # E3/N-5 检出力反向验证：若失效逻辑缺席（patch 为 no-op），同一序列
        # 必然读到过期 [] —— 证明上一条测试具备回归检出能力
        cp, inner, _redis = self._make()
        await cp.aput(_PARENT_CFG, _make_tuple().checkpoint, {"step": 1}, {})
        pending = [("__interrupt__", {"idx": 1})]
        inner.store["c1"] = _make_tuple(pending=pending)
        with mock.patch.object(cp, "_cache_delete", new_callable=mock.AsyncMock):
            await cp.aput_writes(_CFG, pending, "task-1")
        t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 0, "未失效则缓存命中不回源")
        self.assertEqual(t.pending_writes, [], "未失效时读到过期空 pending_writes（即 N-5 缺陷形态）")

    async def test_aput_writes_redis_failure_does_not_break(self):
        # Redis 失效失败仅 debug 日志（TTL 兜底），不中断 PG 写路径
        cp, inner, _redis = self._make(redis=create_fake_redis(fail=True))
        await cp.aput_writes(_CFG, [("__interrupt__", {"idx": 1})], "task-1")  # 不应抛出
        self.assertEqual(inner.aput_writes_calls, 1, "PG 写结果不受 Redis 失败影响")

    async def test_aput_writes_without_checkpoint_id_skips_invalidation(self):
        # 防御：config 无 checkpoint_id 时无法定位缓存键，跳过失效且不误删他键
        cp, inner, redis = self._make()
        redis._store[cp._cache_key("t1", "c1")] = b"unrelated-entry"
        await cp.aput_writes({"configurable": {"thread_id": "t1"}}, [("c", 1)], "task-1")
        self.assertEqual(inner.aput_writes_calls, 1)
        self.assertIn(cp._cache_key("t1", "c1"), redis._store, "无法定位键时不误删")

    async def test_sync_put_writes_delegates_to_inner(self):
        # 写路径枚举收尾：同步 put_writes 委托 inner（AsyncPostgresSaver 同步方法
        # 抛 NotImplementedError → PG 不落盘），无缓存不一致面
        cp, inner, _redis = self._make()
        cp.put_writes(_CFG, [("messages", "x")], "task-1")
        self.assertEqual(inner.put_writes_calls, 1, "同步 put_writes 委托 inner")

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
        cp, inner, _redis = self._make(redis=create_fake_redis(fail=True))
        inner.store["c1"] = _make_tuple()
        t = await cp.aget_tuple(_CFG)
        self.assertEqual(inner.aget_calls, 1, "Redis 宕机回源 inner")
        self.assertIsNotNone(t, "Redis 宕机不中断，返回 PG 结果")

    async def test_aget_tuple_lock_failure_direct_fallback(self):
        # 锁不可用（Redis 抛错）→ 不阻塞等待，直接回源 inner
        cp, inner, _ = self._make(redis=create_fake_redis(fail=True))
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

    async def test_adelete_thread_purges_redis_keys(self):
        # E1f：PG 删除成功后主动清理该 thread 的缓存键 + 锁键，不影响其他 thread
        redis = _ScanFakeRedis()
        cp, inner, redis = self._make(redis=redis)
        await cp.aput(_PARENT_CFG, _make_tuple().checkpoint, {"step": 1}, {})
        redis._store[cp._lock_key("t1", "c1")] = b"1"  # 预置残留限流锁键
        other_key = cp._cache_key("t2", "c9")
        redis._store[other_key] = b"stale"  # 跨 thread 键
        await cp.adelete_thread("t1")
        self.assertEqual(inner.adelete_calls, 1)
        self.assertEqual(inner.store, {}, "inner PG 已删除")
        self.assertNotIn(cp._cache_key("t1", "c1"), redis._store, "缓存键已主动清理")
        self.assertNotIn(cp._lock_key("t1", "c1"), redis._store, "锁键已主动清理")
        self.assertIn(other_key, redis._store, "其他 thread 的键不受影响")
        self.assertEqual(
            redis.scan_matches,
            [f"{CACHE_KEY_PREFIX}:t1:*", f"{LOCK_KEY_PREFIX}:t1:*"],
            "scan_iter 分别按缓存/锁前缀扫描（禁用 KEYS）",
        )
        self.assertEqual(
            set(redis.deleted_calls[0]),
            {cp._cache_key("t1", "c1"), cp._lock_key("t1", "c1")},
            "批量 delete 收到扫描到的匹配键",
        )

    async def test_adelete_thread_no_matching_keys_skips_delete(self):
        redis = _ScanFakeRedis()
        cp, inner, redis = self._make(redis=redis)
        await cp.adelete_thread("t1")
        self.assertEqual(inner.adelete_calls, 1, "PG 删除照常执行")
        self.assertEqual(redis.scan_matches, [f"{CACHE_KEY_PREFIX}:t1:*", f"{LOCK_KEY_PREFIX}:t1:*"])
        self.assertEqual(redis.deleted_calls, [], "无匹配键时不调用 delete")

    async def test_adelete_thread_redis_failure_degrades_with_warning(self):
        # Redis 清理失败不中断删除流程：PG 已删 + warning 日志 + 无异常
        redis = _ScanFakeRedis(fail=True)
        cp, inner, redis = self._make(redis=redis)
        await cp.aput(_PARENT_CFG, _make_tuple().checkpoint, {"step": 1}, {})
        with self.assertLogs(
            "Django_xm.apps.ai_engine.services.redis_cached_checkpointer", level="WARNING"
        ) as logs:
            await cp.adelete_thread("t1")  # 不应抛出
        self.assertEqual(inner.adelete_calls, 1, "Redis 失败时 PG 删除已完成")
        self.assertTrue(logs.output, "清理失败记录 warning 日志")

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
