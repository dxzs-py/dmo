"""Redis 热缓存 Checkpointer 装饰器（Phase 2，D3/D4/D5/D7/D8）。

装饰 ``AsyncPostgresSaver``，在 PG 之上增加 Redis 单点读缓存：
- 写路径：先写 inner（PG）成功，再写 Redis；Redis 写失败仅 debug 日志。
- 读路径：先读 Redis，命中返回；miss 回源 inner，成功后带限流锁回填。
- ``aput_writes``（interrupt 等场景的 pending writes）先写 inner，成功后失效
  对应 (thread_id, checkpoint_id) 的缓存键（E3/N-5）：aput 回填的缓存固定
  pending_writes=[]，若 put_writes 族不失效缓存，显式按 checkpoint_id 的读
  将拿到过期的空 pending_writes。
- ``alist`` 直接透传 inner，不缓存（list 版本持续增长，缓存维护成本高）。
- ``adelete_thread`` 先删 inner（PG），成功后用 SCAN 主动清理该 thread 的
  Redis 缓存/锁键；清理失败仅 warning（残留键仍由 TTL 过期兜底）。
- 序列化复用 LangGraph ``JsonPlusSerializer``，与 inner（PG）serde 一致；
  反序列化失败丢弃缓存、回源 inner。
- 所有 Redis 操作 try/except 降级，Redis 宕机自动回退 PG。

PG 是唯一真相源，Redis 仅是加速，不是可信数据源。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import Any

from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

# 缓存 key / 锁 key 前缀（直连 Redis，不经过 Django cache，无 KEY_PREFIX）
CACHE_KEY_PREFIX = "checkpoint:cache"
LOCK_KEY_PREFIX = "checkpoint:lock"


class RedisCachedCheckpointer:
    """装饰 AsyncPostgresSaver 的 Redis 单点读缓存装饰器。"""

    def __init__(self, inner: Any, redis_client: Any, ttl: int = 1800, lock_ttl: int = 5):
        self._inner = inner
        self._redis = redis_client
        self._ttl = ttl
        self._lock_ttl = lock_ttl
        self._serde = JsonPlusSerializer()

    # ------------------------------------------------------------------
    # 属性委托：未显式实现的方法（含同步接口 get_tuple/put/list/delete_thread、
    # setup 等）统一委托 inner。agent_hub 全异步后同步接口不会被调用，
    # inner 的同步方法会抛 NotImplementedError（预期行为）。
    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    # ------------------------------------------------------------------
    # 异步接口
    # ------------------------------------------------------------------
    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        """读 checkpoint：缓存命中返回；miss 回源 inner 后带限流锁回填。"""
        thread_id = self._get_thread_id(config)
        checkpoint_id = self._get_checkpoint_id(config)

        # 无明确 checkpoint_id（读最新）不缓存，直接回源 inner，避免维护 latest 指针。
        if not thread_id or not checkpoint_id:
            return await self._inner.aget_tuple(config)

        key = self._cache_key(thread_id, checkpoint_id)

        # 读缓存（命中即返回；反序列化失败/Redis 异常返回 None）
        cached = await self._cache_get(key)
        if cached is not None:
            return cached

        # 回源 inner（PG）
        result = await self._inner.aget_tuple(config)
        if result is None:
            return None

        # 回填缓存（限流锁：抢到才写，抢不到不等待直接返回，避免缓存 stampede）
        if await self._try_acquire_lock(self._lock_key(thread_id, checkpoint_id)):
            await self._cache_set(key, result)
        return result

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        """写 checkpoint：先写 inner（PG）成功，再写 Redis（失败忽略）。"""
        result = await self._inner.aput(config, checkpoint, metadata, new_versions)

        thread_id = self._get_thread_id(result) or self._get_thread_id(config)
        new_checkpoint_id = self._get_checkpoint_id(result)
        parent_checkpoint_id = self._get_checkpoint_id(config)

        if thread_id and new_checkpoint_id:
            tuple_ = CheckpointTuple(
                config=result,
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=(
                    {"configurable": {"thread_id": thread_id, "checkpoint_id": parent_checkpoint_id}}
                    if parent_checkpoint_id
                    else None
                ),
                pending_writes=[],
            )
            await self._cache_set(self._cache_key(thread_id, new_checkpoint_id), tuple_)
        return result

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """写 pending writes（interrupt 挂起等场景）：先写 inner（PG）成功，
        再失效该 (thread_id, checkpoint_id) 的缓存键（E3/N-5 根因修复）。

        ``aput`` 回填缓存时 pending_writes 固定 []——对新 checkpoint 属实（写入
        尚未发生）；但本方法落库后 PG 的 pending_writes 已变化，若不失效缓存，
        显式按 checkpoint_id 的 aget_tuple 将命中过期缓存读到空 pending_writes。
        失效采用删键而非回填：单 checkpoint 的 pending_writes 是全部 task 写入
        的并集，拼装语义属于 inner（PG），删键后下次读 miss 回源 PG 重建真值。

        写路径全枚举对照：
        - aput：覆盖（缓存新 checkpoint，当时 pending_writes=[] 属真）；
        - aput_writes：本方法（写后失效）；
        - put_writes（同步）：经 __getattr__ 委托 inner；AsyncPostgresSaver 的
          同步方法抛 NotImplementedError（见模块 docstring 契约），PG 不落盘，
          不产生缓存不一致面（异步 Redis 客户端也无法在同步方法中安全失效）；
        - adelete_thread：已覆盖（SCAN 清理该 thread 全部缓存/锁键）。
        """
        await self._inner.aput_writes(config, writes, task_id, task_path)

        thread_id = self._get_thread_id(config)
        checkpoint_id = self._get_checkpoint_id(config)
        if thread_id and checkpoint_id:
            await self._cache_delete(self._cache_key(thread_id, checkpoint_id))

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ):
        """列出历史 checkpoint 版本：直接透传 inner，不读写 Redis。"""
        async for item in self._inner.alist(config, filter=filter, before=before, limit=limit):
            yield item

    async def adelete_thread(self, thread_id: str) -> None:
        """删除 thread：先删 inner（PG，唯一真相源），成功后主动清理该 thread
        的全部 Redis 键（缓存 + 限流锁）；Redis 清理失败仅 warning，不影响
        删除结果（残留键由 TTL 过期兜底）。"""
        await self._inner.adelete_thread(thread_id)
        await self._purge_thread_redis_keys(thread_id)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _get_thread_id(config: dict[str, Any] | None) -> str | None:
        return (config or {}).get("configurable", {}).get("thread_id")

    @staticmethod
    def _get_checkpoint_id(config: dict[str, Any] | None) -> str | None:
        return (config or {}).get("configurable", {}).get("checkpoint_id")

    @staticmethod
    def _cache_key(thread_id: str, checkpoint_id: str) -> str:
        return f"{CACHE_KEY_PREFIX}:{thread_id}:{checkpoint_id}"

    @staticmethod
    def _lock_key(thread_id: str, checkpoint_id: str) -> str:
        return f"{LOCK_KEY_PREFIX}:{thread_id}:{checkpoint_id}"

    @staticmethod
    def _tuple_to_dict(tuple_: CheckpointTuple) -> dict[str, Any]:
        return {
            "config": tuple_.config,
            "checkpoint": tuple_.checkpoint,
            "metadata": tuple_.metadata,
            "parent_config": tuple_.parent_config,
            "pending_writes": tuple_.pending_writes,
        }

    async def _cache_get(self, key: str) -> CheckpointTuple | None:
        """读缓存；反序列化失败或 Redis 异常返回 None（丢弃缓存回源）。"""
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            meta = json.loads(raw.decode("utf-8"))
            data = self._serde.loads_typed((meta["t"], base64.b64decode(meta["d"])))
            return CheckpointTuple(**data)
        except Exception as e:
            logger.debug(f"[RedisCachedCheckpointer] 缓存读取/反序列化失败，回源 PG: {e}")
            return None

    async def _cache_set(self, key: str, tuple_: CheckpointTuple) -> None:
        """写缓存；Redis 写失败仅 debug 日志（PG 已落盘，缓存 miss 回源 PG）。"""
        try:
            type_str, data_bytes = self._serde.dumps_typed(self._tuple_to_dict(tuple_))
            payload = json.dumps(
                {"t": type_str, "d": base64.b64encode(data_bytes).decode("ascii")}
            ).encode("utf-8")
            await self._redis.set(key, payload, ex=self._ttl)
        except Exception as e:
            logger.debug(f"[RedisCachedCheckpointer] 缓存写入失败（忽略）: {e}")

    async def _cache_delete(self, key: str) -> None:
        """失效单个缓存键；Redis 失败仅 debug 日志（PG 已落盘，miss 回源兜底，
        残留键由 TTL 过期兜底，不向上抛错中断写路径）。"""
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.debug(f"[RedisCachedCheckpointer] 缓存失效删除失败（忽略）: {e}")

    async def _purge_thread_redis_keys(self, thread_id: str) -> None:
        """删除 thread 相关的全部 Redis 键（缓存 + 限流锁），供 adelete_thread 调用。

        使用 SCAN（scan_iter）游标遍历匹配键后批量 delete——禁用 KEYS，
        避免生产 Redis 阻塞；任何 Redis 异常降级为 warning（PG 是唯一真相源，
        删除已成功，残留键由 TTL 过期兜底）。
        """
        try:
            keys: list[str] = []
            for pattern in (
                f"{CACHE_KEY_PREFIX}:{thread_id}:*",
                f"{LOCK_KEY_PREFIX}:{thread_id}:*",
            ):
                async for key in self._redis.scan_iter(match=pattern):
                    keys.append(key)
            if keys:
                await self._redis.delete(*keys)
        except Exception as e:
            logger.warning(f"[RedisCachedCheckpointer] thread={thread_id} Redis 残留键清理失败（TTL 兜底）: {e}")

    async def _try_acquire_lock(self, key: str) -> bool:
        """尝试获取回源限流锁（SET NX EX）；失败不等待，返回 False 直接回源。"""
        try:
            acquired = await self._redis.set(key, "1", nx=True, ex=self._lock_ttl)
            return bool(acquired)
        except Exception as e:
            logger.debug(f"[RedisCachedCheckpointer] 抢锁失败（忽略）: {e}")
            return False
