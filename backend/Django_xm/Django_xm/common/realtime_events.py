"""
统一实时事件总线

为 WebSocket 实时同步提供发布、持久化和回放能力。
- 用户级事件：user_{user_id}
- 会话级事件：session_{session_id}
- 任务级事件：task_{task_id}（独立深度研究场景，无 chat_session_id 关联）
- 序列号生成：Redis INCR
- 历史事件：Redis List，单通道最多保留 500 条

事件格式（WebSocket）：
{
    "type": "session_created",
    "seq": 1,
    "timestamp": 1234567890.123,
    "payload": { ... }
}

统一事件发布接口
================

提供唯一的 ``publish_event`` 函数作为实时事件发布入口，
所有实时事件必须通过此函数发布。事件类型由 ``EventType`` 枚举指定，
payload 必须符合 ``event_schema.py`` 中的 TypedDict 定义。

- ``publish_event``：统一事件发布入口（async），按 ``EventType`` 路由到对应 WebSocket 频道。
  payload 由 ``validate_payload`` 强制校验，不允许 ``**extra`` 开放透传。
- ``publish_event_sync``：同步版本，用于 Celery worker 等同步上下文。

底层实现函数（模块内部使用，不对外暴露）：
- ``_publish_to_session_async``：发布会话级事件
- ``_publish_to_task_async``：发布任务级事件
- ``_publish_to_user_async``：发布用户级事件

频道路由规则
------------
1. ``session_id`` 非空：广播到 ``session:{session_id}`` 分组
2. ``task_id`` 非空：广播到 ``task:{task_id}`` 分组
3. ``user_id`` 非空：广播到 ``user:{user_id}`` 分组
4. 三者可同时存在（多频道广播）

注意：``seq`` 与 ``timestamp`` 由底层 ``_publish_to_session_async`` /
``_publish_to_task_async`` / ``_publish_to_user_async`` 注入到事件顶层
（与 payload 平级），并非 payload 字段。

深度研究关联 chat 场景的频道路由由 ``realtime_sync._resolve_channels`` 统一处理，
返回 ``(chat_session_id, task_id)`` 二元组，``publish_event`` 同时广播到 session 和
task 频道，无需依赖额外的双频道广播机制。
"""

import asyncio
import json
import time
import logging
import threading
from typing import Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

from Django_xm.common.event_schema import (
    EventType,
    EventSource,
    PayloadValidationError,
    validate_payload,
    get_ws_event_name,
    is_tool_lifecycle_event,
    is_approval_event,
    is_stream_event,
)


logger = logging.getLogger(__name__)


EVENT_HISTORY_LIMIT = 500

# 历史事件 key TTL：24 小时，避免 Redis 内存无限增长
HISTORY_TTL_SECONDS = 24 * 60 * 60

# seq key TTL：24 小时，避免内存泄漏
SEQ_TTL_SECONDS = 24 * 60 * 60

# Redis key 前缀
SEQ_KEY_PREFIX = "realtime:seq"
HISTORY_KEY_PREFIX = "realtime:history"


def _get_redis_client():
    """获取默认缓存底层的 Redis 客户端。"""
    return cache.client.get_client()


def _seq_key(channel_type, channel_id):
    return f"{SEQ_KEY_PREFIX}:{channel_type}:{channel_id}"


def _history_key(channel_type, channel_id):
    return f"{HISTORY_KEY_PREFIX}:{channel_type}:{channel_id}"


def _group_name(channel_type, channel_id):
    """生成符合 Channels 规范的 group name（只含 ASCII 字母数字、连字符、下划线、句点，<100 字符）。"""
    base = f"{channel_type}_{channel_id}"
    # 替换冒号、空格等非规范字符为下划线
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in base)
    # 超长时截断并用 hash 保证唯一性
    if len(safe) >= 100:
        import hashlib
        suffix = hashlib.md5(safe.encode('utf-8')).hexdigest()[:16]
        safe = f"{safe[:80]}_{suffix}"
    return safe


# Lua 脚本：原子化 INCR + RPUSH + LTRIM + EXPIRE
# KEYS[1] = seq key, KEYS[2] = history key
# ARGV[1] = event JSON（不含 seq），ARGV[2] = history TTL，ARGV[3] = seq TTL，ARGV[4] = history limit
_PUBLISH_EVENT_LUA = """
local seq = redis.call('INCR', KEYS[1])
local event = string.gsub(ARGV[1], '^{', '{"seq":' .. seq .. ',', 1)
redis.call('RPUSH', KEYS[2], event)
redis.call('LTRIM', KEYS[2], -ARGV[4], -1)
redis.call('EXPIRE', KEYS[2], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return seq
"""

# 已注册的 Lua 脚本对象（懒加载，避免模块导入时访问 Redis）
_PUBLISH_EVENT_LUA_SCRIPT = None


def _get_publish_event_script(redis_client):
    """懒加载注册 Lua 脚本，失败时返回 None 以回退到 eval。"""
    global _PUBLISH_EVENT_LUA_SCRIPT
    if _PUBLISH_EVENT_LUA_SCRIPT is None:
        try:
            _PUBLISH_EVENT_LUA_SCRIPT = redis_client.register_script(_PUBLISH_EVENT_LUA)
        except Exception as e:
            logger.warning(f"[RealtimeEvents] 注册 Lua 脚本失败，将使用 eval: {e}")
            _PUBLISH_EVENT_LUA_SCRIPT = False
    return _PUBLISH_EVENT_LUA_SCRIPT if _PUBLISH_EVENT_LUA_SCRIPT is not False else None


def _atomic_publish_event(redis_client, channel_type, channel_id, event):
    """原子化生成 seq 并持久化事件到 Redis List。

    通过 Lua 脚本保证 INCR + RPUSH + LTRIM + EXPIRE 的原子性，
    seq 由 Lua 脚本注入到持久化的事件 JSON 中。

    Args:
        redis_client: Redis 客户端
        channel_type: 'user' 或 'session'
        channel_id: 用户 ID 或会话 ID
        event: 不含 seq 字段的事件 dict

    Returns:
        int: 生成的序列号 seq
    """
    seq_key = _seq_key(channel_type, channel_id)
    history_key = _history_key(channel_type, channel_id)
    # 构建不含 seq 的事件 JSON，由 Lua 脚本注入 seq 字段
    event_without_seq = {k: v for k, v in event.items() if k != "seq"}
    event_json = json.dumps(event_without_seq, ensure_ascii=False)

    script = _get_publish_event_script(redis_client)
    if script is not None:
        seq = script(
            keys=[seq_key, history_key],
            args=[event_json, HISTORY_TTL_SECONDS, SEQ_TTL_SECONDS, EVENT_HISTORY_LIMIT],
            client=redis_client,
        )
    else:
        seq = redis_client.eval(
            _PUBLISH_EVENT_LUA, 2,
            seq_key, history_key,
            event_json, str(HISTORY_TTL_SECONDS),
            str(SEQ_TTL_SECONDS), str(EVENT_HISTORY_LIMIT),
        )
    return int(seq)


async def _publish_to_session_async(session_id, event_type, payload):
    """异步实际发布会话事件到 WebSocket + Redis 持久化。

    底层实现函数，由 publish_event（异步路径）调用。

    Args:
        session_id: 会话 ID
        event_type: 事件类型（ws_event_name 字符串）
        payload: 事件载荷 dict
    """
    try:
        redis_client = _get_redis_client()
        # 在 event 顶层注入 session_id（路由字段），供前端 dispatchEvent 路由使用
        # 前端 useRealtimeSync.dispatchEvent 通过 event.payload?.session_id || event.session_id 解析通道
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "payload": payload or {},
            "session_id": session_id,
        }

        # 原子化生成 seq 并持久化（seq 由 Lua 脚本注入到持久化事件中）
        seq = _atomic_publish_event(redis_client, "session", session_id, event)
        event["seq"] = seq

        logger.debug(
            f"[RealtimeEvents] 连接池监控(async): session={session_id}, "
            f"type={event_type}, seq={seq}, payload_keys={list((payload or {}).keys())}"
        )

        # group_send 失败不回滚 seq（已持久化），仅记录日志
        try:
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                _group_name("session", session_id),
                {"type": "broadcast_event", "event": event},
            )
        except Exception as e:
            logger.error(f"[RealtimeEvents] group_send 失败: session={session_id}, {e}")

        logger.info(
            f"[RealtimeEvents] 异步发布会话事件: session={session_id}, "
            f"type={event_type}, seq={seq}"
        )
    except Exception as e:
        logger.error(
            f"[RealtimeEvents] _publish_to_session_async 失败: session={session_id}, "
            f"type={event_type}, {e}"
        )


async def _publish_to_task_async(task_id, event_type, payload):
    """异步实际发布任务事件到 WebSocket + Redis 持久化。

    与 _publish_to_session_async 对齐，区别仅在于 channel_type 为 "task"，
    channel_id 为 task_id。

    Args:
        task_id: 研究任务 ID（ResearchTask.task_id）
        event_type: 事件类型
        payload: 事件载荷 dict
    """
    try:
        redis_client = _get_redis_client()
        # 在 event 顶层注入 task_id（路由字段），供前端 dispatchEvent 路由使用
        # 前端 useRealtimeSync.dispatchEvent 通过 event.payload?.task_id 解析 task 通道
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "payload": payload or {},
            "task_id": task_id,
        }

        # 原子化生成 seq 并持久化（seq 由 Lua 脚本注入到持久化事件中）
        # Redis key：realtime:seq:task:{task_id} / realtime:history:task:{task_id}
        seq = _atomic_publish_event(redis_client, "task", task_id, event)
        event["seq"] = seq

        logger.debug(
            f"[RealtimeEvents] 连接池监控(async): task={task_id}, "
            f"type={event_type}, seq={seq}, payload_keys={list((payload or {}).keys())}"
        )

        # group_send 失败不回滚 seq（已持久化），仅记录日志
        try:
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                _group_name("task", task_id),
                {"type": "broadcast_event", "event": event},
            )
        except Exception as e:
            logger.error(f"[RealtimeEvents] group_send 失败: task={task_id}, {e}")

        logger.info(
            f"[RealtimeEvents] 异步发布任务事件: task={task_id}, "
            f"type={event_type}, seq={seq}"
        )
    except Exception as e:
        logger.error(
            f"[RealtimeEvents] _publish_to_task_async 失败: task={task_id}, "
            f"type={event_type}, {e}"
        )


async def _publish_to_user_async(user_id, event_type, payload):
    """异步发布用户事件到 WebSocket + Redis 持久化。

    与 _publish_to_session_async 对齐，区别在于 channel_type 为 "user"，
    channel_id 为 user_id。

    Args:
        user_id: 用户 ID
        event_type: 事件类型（ws_event_name 字符串）
        payload: 事件载荷 dict
    """
    try:
        redis_client = _get_redis_client()
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "payload": payload or {},
        }

        # 原子化生成 seq 并持久化（seq 由 Lua 脚本注入到持久化事件中）
        seq = _atomic_publish_event(redis_client, "user", user_id, event)
        event["seq"] = seq

        logger.debug(
            f"[RealtimeEvents] 连接池监控(async): user={user_id}, "
            f"type={event_type}, seq={seq}, payload_keys={list((payload or {}).keys())}"
        )

        # group_send 失败不回滚 seq（已持久化），仅记录日志
        try:
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                _group_name("user", user_id),
                {"type": "broadcast_event", "event": event},
            )
        except Exception as e:
            logger.error(f"[RealtimeEvents] group_send 失败: user={user_id}, {e}")

        logger.info(
            f"[RealtimeEvents] 异步发布用户事件: user={user_id}, "
            f"type={event_type}, seq={seq}"
        )
    except Exception as e:
        logger.error(
            f"[RealtimeEvents] _publish_to_user_async 失败: user={user_id}, "
            f"type={event_type}, {e}"
        )


async def publish_event(
    event_type: EventType,
    payload: dict,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """统一事件发布接口（唯一入口）。

    所有实时事件通过此函数发布，按频道路由规则分发到 WebSocket 频道。
    事件类型由 ``EventType`` 枚举指定，payload 必须符合 ``event_schema.py``
    中的 TypedDict 定义，由 ``validate_payload`` 强制校验。

    串行化与 seq 顺序保证：
        通过 ``_ordered_ensure_future`` 按 channel 串行化事件发布，确保
        同一 channel 的事件按调用顺序执行。Redis Lua 脚本生成 seq 的顺序
        与 group_send 广播顺序一致，前端按 seq 处理事件时不会出现状态覆盖。

        - 同一 channel 的事件严格按调用顺序串行执行，seq 严格单调递增
          （允许跳号，不允许乱序）
        - 不同 channel 的事件并行执行，互不阻塞
        - 调用方 ``await publish_event(...)`` 会等待所有目标 channel 的
          发布任务完成后返回

    Args:
        event_type: 事件类型（EventType 枚举值）
        payload: 事件 payload dict（必须符合 event_schema.py 中的 TypedDict 定义）
        session_id: 会话 ID，非空时发布到 session:{session_id} 频道；
            chat 场景为 session_id，learning 场景为 thread_id，
            deep_research 关联 chat 场景为 chat_session_id（由 realtime_sync._resolve_channels
            解析后传入）
        task_id: 任务 ID，非空时发布到 task:{task_id} 频道；
            独立深度研究场景（无 chat_session_id 关联）使用
        user_id: 用户 ID，非空时发布到 user:{user_id} 频道；
            用于会话列表变更等用户级事件

    频道路由规则：
        1. session_id 非空 → session:{session_id}
        2. task_id 非空 → task:{task_id}
        3. user_id 非空 → user:{user_id}
        4. 三者可同时存在（多频道广播）

    payload 校验：
        - 必须是 dict
        - 必填字段必须存在且非 None（由 _REQUIRED_FIELDS 定义）
        - source 字段必须是 EventSource 枚举值或合法字符串
        - 校验失败时记录 ERROR 并抛出 PayloadValidationError，调用方 MUST 捕获
          并决定降级策略（不再静默 return，避免事件被丢弃且调用方无感知）

    WebSocket 事件名映射：
        每个 EventType 使用其 value 作为独立 ws_event_name，
        前端通过 event.type 直接区分事件子类型（不再统一映射为
        tool_call_changed / approval_changed）。
        - 工具事件：tool_call_pending / tool_call_input_ready / tool_call_running /
          tool_call_completed / tool_call_failed / tool_call_timeout 等
        - 审批事件：approval_pending / approval_processing / approval_approved /
          approval_rejected / approval_timeout 等
        - 流式事件：stream_event（reasoning/sources/suggestions/context/content_update）
          / stream_started / stream_completed / stream_finalized
        - 会话/消息事件：session_created / message_added / message_updated 等

    注意：
        - ``seq`` 与 ``timestamp`` 由底层 ``_publish_to_session_async`` /
          ``_publish_to_task_async`` / ``_publish_to_user_async`` 注入到事件顶层
          （与 payload 平级），不在 payload 中
        - 本函数为 async，同步上下文请使用 ``publish_event_sync``
    """
    # 1. 校验 payload（approval 等事件的必填字段校验由 validate_payload 统一处理）
    # 校验失败时记录 ERROR 并抛出 PayloadValidationError，调用方 MUST 捕获并决定降级策略
    # 禁止静默 return，否则调用方无感知、事件被丢弃（问题11根因）
    try:
        validate_payload(event_type, payload)
    except PayloadValidationError:
        logger.error(
            f"[RealtimeEvents] publish_event payload 校验失败，事件未发布: "
            f"event_type={event_type}, session_id={session_id}, task_id={task_id}, "
            f"payload_keys={list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}",
            exc_info=True
        )
        raise  # 抛给调用方，由调用方决定降级策略

    # 注入 event_type 字段到 payload，供前端区分 stream_event 子类型
    # （stream_reasoning / stream_sources / stream_suggestions / stream_context / stream_content_update）
    # tool_call_* 和 approval_* 事件通过 event.type 区分，event_type 字段为冗余信息但不影响处理
    payload = {**payload, 'event_type': event_type.value}

    # 2. 获取 WebSocket 频道事件名（每个 EventType 使用其 value 作为独立 ws_event_name）
    ws_event_name = get_ws_event_name(event_type)

    # 3. 频道路由：按 channel 串行化发布，确保同一 channel 的 seq 顺序与调用顺序一致
    try:
        logger.debug(
            f"[RealtimeEvents] publish_event: event_type={event_type.value}, "
            f"ws_name={ws_event_name}, session={session_id}, task={task_id}, "
            f"user={user_id}, payload_keys={list(payload.keys())}"
        )

        # 收集各 channel 的串行化 task，不同 channel 之间并行
        channel_tasks = []

        # 发布到 session 频道（如果有 session_id）
        if session_id:
            channel_tasks.append(
                _ordered_ensure_future(
                    _publish_to_session_async(session_id, ws_event_name, payload),
                    f"session:{session_id}",
                )
            )
            logger.info(
                f"[RealtimeEvents] 发布事件到 session 频道: session={session_id}, "
                f"event_type={event_type.value}, ws_name={ws_event_name}"
            )

        # 发布到 task 频道（如果有 task_id，独立深度研究场景）
        if task_id:
            channel_tasks.append(
                _ordered_ensure_future(
                    _publish_to_task_async(task_id, ws_event_name, payload),
                    f"task:{task_id}",
                )
            )
            logger.info(
                f"[RealtimeEvents] 发布事件到 task 频道: task={task_id}, "
                f"event_type={event_type.value}, ws_name={ws_event_name}"
            )

        # 发布到 user 频道（如果有 user_id，用户级事件）
        if user_id:
            channel_tasks.append(
                _ordered_ensure_future(
                    _publish_to_user_async(user_id, ws_event_name, payload),
                    f"user:{user_id}",
                )
            )
            logger.info(
                f"[RealtimeEvents] 发布事件到 user 频道: user={user_id}, "
                f"event_type={event_type.value}, ws_name={ws_event_name}"
            )

        # 等待所有 channel 的发布任务完成
        # 不同 channel 并行执行，同一 channel 内部由 _ordered_ensure_future 串行化
        if channel_tasks:
            await asyncio.gather(*channel_tasks, return_exceptions=True)
    except Exception as e:
        logger.error(
            f"[RealtimeEvents] publish_event 失败: event_type={event_type}, "
            f"session={session_id}, task={task_id}, user={user_id}, {e}"
        )


# 持有 fire-and-forget 发布任务引用，防止 GC 回收未完成的任务
_pending_publish_tasks = set()


# 按 channel 维护有序 task 链，保证同一 channel 的事件按调用顺序发布
# 核心机制：publish_event 内部通过 _ordered_ensure_future 将每个 channel 的
# _publish_to_*_async 协程包装进 task 链，确保 Redis Lua 脚本生成 seq 的顺序
# 与 group_send 广播顺序一致。同一 channel 严格串行（seq 严格单调递增），
# 不同 channel 并行。前端按 seq 处理事件时不会出现状态覆盖。
_publish_chains = {}  # key: channel_key, value: asyncio.Task
_publish_chains_lock = threading.Lock()


def _ordered_ensure_future(coro, channel_key):
    """按 channel 串行化 async 任务，保证同一 channel 的任务按调用顺序执行。

    核心机制：每个 channel 维护一个 task 链，新 task 必须等待前一个 task 完成后才开始执行。
    这样确保同一 channel 的 _atomic_publish_event（Redis Lua 脚本生成 seq）按调用顺序执行，
    seq 顺序与调用顺序一致，前端按 seq 处理事件时不会出现状态覆盖。

    同时支持 sync 调用方（fire-and-forget）与 async 调用方（await 返回的 task）：
    - sync 调用方（如 publish_event_sync）：仅持有 task 引用，不 await
    - async 调用方（如 publish_event）：await 返回的 task，等待本 channel 发布完成

    Args:
        coro: 协程对象
        channel_key: 通道标识（格式为 "session:{id}" / "task:{id}" / "user:{id}"）

    Returns:
        asyncio.Task: 新创建的 task
    """
    loop = asyncio.get_running_loop()

    async def _chained_execute(prev_task):
        # 等待前一个 task 完成，确保 seq 生成顺序与调用顺序一致
        if prev_task is not None:
            try:
                await prev_task
            except Exception:
                # 前一个 task 失败不影响当前 task，仅保证顺序
                pass
        # 执行当前协程
        return await coro

    with _publish_chains_lock:
        prev = _publish_chains.get(channel_key)
        new_task = loop.create_task(_chained_execute(prev))
        _publish_chains[channel_key] = new_task

    _pending_publish_tasks.add(new_task)
    new_task.add_done_callback(_pending_publish_tasks.discard)

    def _cleanup_chain(t):
        # 只有当前链尾是该 task 时才清除，避免清除已更新的新链尾
        with _publish_chains_lock:
            if _publish_chains.get(channel_key) is t:
                _publish_chains.pop(channel_key, None)

    new_task.add_done_callback(_cleanup_chain)

    return new_task


def publish_event_sync(
    event_type: EventType,
    payload: dict,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """publish_event 的同步版本（智能适配同步/async 上下文）。

    智能检测当前是否在 async 事件循环中：
    - 如果在 async 事件循环中（如 Django ASGI view 调用同步 generator），
      使用 fire-and-forget 模式调度 ``publish_event`` 协程。串行化与 seq 顺序
      保证由 ``publish_event`` 内部的 ``_ordered_ensure_future`` 按 channel 统一
      负责，本函数不再外层串行化（避免与内层共用 channel_key 导致 task 链死锁）。
      任务引用由 ``_pending_publish_tasks`` 持有，完成后自动清理。
    - 如果不在 async 事件循环中（如 Celery worker 同步任务、Django sync view、
      ``transaction.on_commit`` 回调），使用 ``async_to_sync`` 同步调用 ``publish_event``。

    Args:
        同 publish_event
    """
    try:
        try:
            asyncio.get_running_loop()
            # 在 async 事件循环中，fire-and-forget publish_event
            # 串行化由 publish_event 内部的 _ordered_ensure_future 按 channel 保证，
            # 确保 seq 顺序与调用顺序一致
            task = asyncio.ensure_future(publish_event(
                event_type, payload,
                session_id=session_id,
                task_id=task_id,
                user_id=user_id,
            ))
            _pending_publish_tasks.add(task)
            task.add_done_callback(_pending_publish_tasks.discard)
        except RuntimeError:
            # 不在 async 事件循环中，使用 async_to_sync 同步调用
            async_to_sync(publish_event)(
                event_type, payload,
                session_id=session_id,
                task_id=task_id,
                user_id=user_id,
            )
    except PayloadValidationError:
        # payload 校验失败：记录 ERROR 后重新抛出，由调用方决定降级策略
        # 必须在 except Exception 之前捕获，避免被吞掉导致调用方无感知
        # 注意：仅 async_to_sync 同步路径会触发；fire-and-forget
        # 路径下异常存储于 task，需通过 done_callback 单独处理
        logger.error(
            f"[RealtimeEvents] publish_event_sync payload 校验失败，事件未发布: "
            f"event_type={event_type}, session={session_id}, task={task_id}, "
            f"user={user_id}, payload_keys={list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}",
            exc_info=True
        )
        raise
    except Exception as e:
        logger.error(
            f"[RealtimeEvents] publish_event_sync 失败: event_type={event_type}, "
            f"session={session_id}, task={task_id}, user={user_id}, {e}"
        )


def get_event_history(channel_type, channel_id, last_seq=None, limit=100):
    """获取指定通道的历史事件。

    Args:
        channel_type: 'user' / 'session' / 'task'
        channel_id: 用户 ID / 会话 ID / 研究任务 ID
        last_seq: 若提供，仅返回 seq > last_seq 的事件
        limit: 最大返回条数，默认 100

    Returns:
        list[dict]: 事件列表，按 seq 升序排列
    """
    try:
        redis_client = _get_redis_client()
        key = _history_key(channel_type, channel_id)
        raw_items = redis_client.lrange(key, 0, -1)

        events = []
        for item in raw_items:
            try:
                if isinstance(item, bytes):
                    item = item.decode("utf-8")
                event = json.loads(item)
                if last_seq is not None:
                    if event.get("seq", 0) <= last_seq:
                        continue
                events.append(event)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

        events.sort(key=lambda e: e.get("seq", 0))
        return events[:limit]
    except Exception as e:
        logger.error(f"[RealtimeEvents] 读取历史事件失败: {channel_type}:{channel_id}, {e}")
        return []


def get_channel_seq(channel_type, channel_id):
    """获取指定通道当前的最大 seq。

    用于轮询兜底接口对比本地 last_seq 与服务端最新 seq，判断是否需要回放。

    Args:
        channel_type: 'user' / 'session' / 'task'
        channel_id: 用户 ID / 会话 ID / 研究任务 ID

    Returns:
        int: 当前最大 seq，无事件时返回 0
    """
    try:
        redis_client = _get_redis_client()
        key = _seq_key(channel_type, channel_id)
        raw = redis_client.get(key)
        if raw is None:
            return 0
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return int(raw)
    except Exception as e:
        logger.error(f"[RealtimeEvents] 读取 seq 失败: {channel_type}:{channel_id}, {e}")
        return 0
