"""
统一实时同步 WebSocket 消费者

为聊天应用提供跨浏览器实时事件同步能力。
- 连接时通过 query token 认证
- 默认加入 user:{user_id} 分组，接收用户级事件
- 通过 subscribe_session 动作加入/离开 session:{session_id} 分组
- 接收来自 channel layer 的广播事件并透传给客户端
"""

import asyncio
import json
import logging
import time

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer  # type: ignore[import-untyped]
from django.db import close_old_connections

from Django_xm.common.realtime_events import EVENT_HISTORY_LIMIT, get_event_history
from Django_xm.common.sse_utils import authenticate_websocket_scope

logger = logging.getLogger(__name__)


# WebSocket 单帧 payload 上限（Django Channels 默认 1MB，留 256KB 余量给 envelope）
REPLAY_CHUNK_SIZE_BYTES = 768 * 1024
# 每块最多事件数（防止事件极小但数量极多时单块过大）
REPLAY_MAX_EVENTS_PER_CHUNK = 50

# WebSocket 长连接周期性数据库连接清理间隔（秒）
# 必须大于 CONN_MAX_AGE（10s）才能让 close_old_connections 回收超龄连接
# 每 15s 清理一次，与 SSE 心跳对齐，确保 WS 长连接不长期持有数据库连接
WS_DB_CLEANUP_INTERVAL = 15


async def _send_replay_chunked(consumer, channel_type, channel_id, history):
    """分块发送 replay 消息，避免超过 WebSocket payload 限制。

    统一底层修复（刷新浏览器同步滞后根因）：
    原实现一次性 send_json 500 条事件，序列化后可达 3.86MB，超过 Channels 默认 1MB 限制，
    send_json 抛异常被 except 捕获后仅记录 WARNING，前端永远收不到 replay，
    _processSessionEventOrdered 的 expectedSeq=0 永远等不到 seq=0 的事件，
    所有事件卡在队列直到 5 秒超时——刷新浏览器"慢半拍"。

    分块策略：
    - 按 payload 字节大小（768KB）或事件数（50）任一阈值切分
    - 每块独立 send_json，前端 onMessage 收到 type='replay' 即处理
    - 块间无序依赖：每块的事件按 seq 升序，前端 _processSessionEventOrdered
      会自动按 seq 排序处理（process smallest seq 修复）

    Args:
        consumer: AsyncJsonWebsocketConsumer 实例
        channel_type: 'user' / 'session' / 'task'
        channel_id: 通道 ID
        history: 完整历史事件列表（已按 seq 升序）
    """
    total = len(history)

    if total == 0:
        await consumer.send_json(
            {
                "type": "replay",
                "channel_type": channel_type,
                "channel_id": channel_id,
                "count": 0,
                "chunk_index": 0,
                "chunk_count": 1,
                "events": [],
                "timestamp": time.time(),
            }
        )
        return

    chunks = []
    current_chunk = []
    current_size = 0
    for event in history:
        # 估算单条事件序列化字节大小
        event_size = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
        # 当前块非空且加入后超阈值，则切分
        if current_chunk and (
            current_size + event_size > REPLAY_CHUNK_SIZE_BYTES or len(current_chunk) >= REPLAY_MAX_EVENTS_PER_CHUNK
        ):
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.append(event)
        current_size += event_size
    if current_chunk:
        chunks.append(current_chunk)

    chunk_count = len(chunks)
    for i, chunk in enumerate(chunks):
        await consumer.send_json(
            {
                "type": "replay",
                "channel_type": channel_type,
                "channel_id": channel_id,
                "count": total,
                "chunk_index": i,
                "chunk_count": chunk_count,
                "events": chunk,
                "timestamp": time.time(),
            }
        )
    logger.info(f"[RealtimeSync] 回放分块发送完成: {channel_type}:{channel_id}, total={total}, chunks={chunk_count}")


class RealtimeSyncConsumer(AsyncJsonWebsocketConsumer):
    """
    统一实时同步 WebSocket 消费者

    协议：
    - 连接：ws://host/ws/realtime/?token=<JWT>
    - 客户端 -> 服务端 JSON 消息：{ action, payload }
    - 服务端 -> 客户端 JSON 消息：{ type, seq, timestamp, payload }
    """

    async def connect(self):
        self.user = await sync_to_async(authenticate_websocket_scope)(self.scope)
        if not self.user or not self.user.is_authenticated:
            logger.warning("[RealtimeSync] WebSocket 认证失败，拒绝连接")
            await self.close(code=4001)
            return

        self.user_id = self.user.id
        # Channels group name 必须 < 100 字符且只含 ASCII 字母数字、连字符、下划线、句点
        self.user_group = f"user_{self.user_id}"
        self.session_groups = set()
        # task 通道订阅分组（独立深度研究场景，订阅 task:{task_id}）
        self.task_groups = set()

        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()

        await self.send_json(
            {
                "type": "connected",
                "channel": self.user_group,
                "timestamp": time.time(),
            }
        )
        logger.info(f"[RealtimeSync] 用户 {self.user_id} 已连接 WebSocket")

        # 启动周期性数据库连接清理任务
        # WebSocket 长连接整个会话期间存活，若不定期清理，Django 会因 CONN_MAX_AGE
        # 持续保持数据库连接，N 个并发 WS 连接 = N 个数据库连接长期占用 → 连接池耗尽。
        # 定期调用 close_old_connections() 回收超龄连接（CONN_MAX_AGE=10s < 清理间隔 15s）。
        self._cleanup_task = asyncio.create_task(self._periodic_db_cleanup())

    async def _periodic_db_cleanup(self):
        """周期性清理数据库连接，防止 WebSocket 长连接持有连接池。

        每 WS_DB_CLEANUP_INTERVAL 秒调用 close_old_connections()，
        回收超过 CONN_MAX_AGE 的空闲连接。任务在 disconnect 时取消。
        """
        try:
            while True:
                await asyncio.sleep(WS_DB_CLEANUP_INTERVAL)
                try:
                    await sync_to_async(close_old_connections)()
                except Exception as e:
                    logger.warning(f"[RealtimeSync] 周期性清理连接失败: {e}")
        except asyncio.CancelledError:
            # 正常退出：disconnect 时取消任务
            pass

    async def disconnect(self, close_code):
        # 取消周期性清理任务
        cleanup_task = getattr(self, "_cleanup_task", None)
        if cleanup_task and not cleanup_task.done():
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass

        if hasattr(self, "user_group"):
            try:
                await self.channel_layer.group_discard(self.user_group, self.channel_name)
            except Exception as e:
                logger.warning(f"[RealtimeSync] 离开用户分组失败: {e}")

        for group in getattr(self, "session_groups", set()):
            try:
                await self.channel_layer.group_discard(group, self.channel_name)
            except Exception as e:
                logger.warning(f"[RealtimeSync] 离开会话分组失败: {group}, {e}")

        # 清理 task 通道分组
        for group in getattr(self, "task_groups", set()):
            try:
                await self.channel_layer.group_discard(group, self.channel_name)
            except Exception as e:
                logger.warning(f"[RealtimeSync] 离开任务分组失败: {group}, {e}")

        # WebSocket 断开时清理 DB 连接，防止长连接持有数据库连接导致池耗尽
        await sync_to_async(close_old_connections)()

        logger.info(f"[RealtimeSync] 用户 {getattr(self, 'user_id', '?')} 已断开 WebSocket")

    async def receive_json(self, content, **kwargs):
        action = content.get("action")
        payload = content.get("payload", {})

        handler = getattr(self, f"handle_{action}", self.handle_unknown)

        try:
            await handler(payload)
        except Exception as e:
            logger.exception(f"[RealtimeSync] 处理动作 {action} 失败")
            await self.send_json(
                {
                    "type": "error",
                    "code": "50001",
                    "message": f"处理 {action} 失败: {e!s}",
                    "timestamp": time.time(),
                }
            )

    # ==================== 动作处理 ====================

    async def handle_subscribe_session(self, payload):
        session_id = payload.get("session_id")
        if not session_id:
            await self.send_json(
                {
                    "type": "error",
                    "code": "40002",
                    "message": "缺少 session_id 参数",
                    "timestamp": time.time(),
                }
            )
            return

        if not await self._user_owns_session(session_id):
            await self.send_json(
                {
                    "type": "error",
                    "code": "40401",
                    "message": "会话不存在或无权限",
                    "timestamp": time.time(),
                }
            )
            logger.warning(f"[RealtimeSync] 订阅会话失败(无权限): user={self.user_id}, session={session_id}")
            return

        from Django_xm.common.realtime_events import _group_name

        group = _group_name("session", session_id)
        if group not in self.session_groups:
            await self.channel_layer.group_add(group, self.channel_name)
            self.session_groups.add(group)
            logger.info(f"[RealtimeSync] 用户 {self.user_id} 订阅会话: session={session_id}, group={group}")

        await self.send_json(
            {
                "type": "subscribed",
                "channel": group,
                "timestamp": time.time(),
            }
        )

        # 按需回放历史事件
        # 传 limit=EVENT_HISTORY_LIMIT（500）确保回放完整，避免因默认 limit=100
        # 导致 session 历史事件超过 100 条时回放不完整，前端 _processSessionEventOrdered
        # 因缺失中间事件而卡住，跨浏览器工具调用/审批状态无法同步（统一底层修复）
        #
        # 统一底层修复（Z1/Z2/Z3/Z4/Z5 + 刷新慢半拍）：
        # 使用 _send_replay_chunked 分块发送，避免 500 条事件（3.86MB）超过
        # WebSocket 1MB payload 限制导致 send_json 失败（刷新浏览器永远收不到 replay）。
        # 前端 useRealtimeSync barrier 机制保证 replay 期间实时事件被缓冲，
        # replay 完成后再按 seq 顺序处理，避免 Z1-Z5 竞态。
        last_seq = payload.get("last_seq")
        if last_seq is not None:
            try:
                last_seq = int(last_seq)
                history = await sync_to_async(get_event_history)(
                    "session", session_id, last_seq, limit=EVENT_HISTORY_LIMIT
                )
                logger.info(
                    f"[RealtimeSync] 回放会话历史: session={session_id}, last_seq={last_seq}, count={len(history)}, limit={EVENT_HISTORY_LIMIT}"
                )
                await _send_replay_chunked(self, "session", session_id, history)
            except Exception as e:
                logger.warning(f"[RealtimeSync] 回放会话历史失败: {session_id}, {e}")
            finally:
                # 回放完成后清理 DB 连接，防止历史查询残留连接
                await sync_to_async(close_old_connections)()

    async def handle_unsubscribe_session(self, payload):
        session_id = payload.get("session_id")
        if not session_id:
            await self.send_json(
                {
                    "type": "error",
                    "code": "40002",
                    "message": "缺少 session_id 参数",
                    "timestamp": time.time(),
                }
            )
            return

        from Django_xm.common.realtime_events import _group_name

        group = _group_name("session", session_id)
        if group in self.session_groups:
            await self.channel_layer.group_discard(group, self.channel_name)
            self.session_groups.discard(group)

        await self.send_json(
            {
                "type": "unsubscribed",
                "channel": group,
                "timestamp": time.time(),
            }
        )

    async def handle_subscribe_task(self, payload):
        """订阅 task 通道（独立深度研究场景）。

        协议：
            { "action": "subscribe_task", "payload": { "task_id": "...", "last_seq": 0 } }

        权限校验：调用 _user_owns_task 确认当前用户为 ResearchTask 的创建者。
        加入 task:{task_id} 分组，并按需回放历史事件。
        """
        task_id = payload.get("task_id")
        if not task_id:
            await self.send_json(
                {
                    "type": "error",
                    "code": "40002",
                    "message": "缺少 task_id 参数",
                    "timestamp": time.time(),
                }
            )
            return

        if not await self._user_owns_task(task_id):
            await self.send_json(
                {
                    "type": "error",
                    "code": "40401",
                    "message": "任务不存在或无权限",
                    "timestamp": time.time(),
                }
            )
            return

        from Django_xm.common.realtime_events import _group_name

        group = _group_name("task", task_id)
        if group not in self.task_groups:
            await self.channel_layer.group_add(group, self.channel_name)
            self.task_groups.add(group)

        await self.send_json(
            {
                "type": "subscribed",
                "channel": group,
                "timestamp": time.time(),
            }
        )

        # 按需回放历史事件
        # 统一底层修复（Z1/Z2/Z3/Z4/Z5 + 刷新慢半拍）：与 handle_subscribe_session 保持一致，
        # 使用 _send_replay_chunked 分块发送，避免超过 WebSocket payload 限制。
        last_seq = payload.get("last_seq")
        if last_seq is not None:
            try:
                last_seq = int(last_seq)
                history = await sync_to_async(get_event_history)("task", task_id, last_seq, limit=EVENT_HISTORY_LIMIT)
                await _send_replay_chunked(self, "task", task_id, history)
            except Exception as e:
                logger.warning(f"[RealtimeSync] 回放任务历史失败: task_id={task_id}, {e}")
            finally:
                # 回放完成后清理 DB 连接
                await sync_to_async(close_old_connections)()

    async def handle_unsubscribe_task(self, payload):
        """离开 task:{task_id} 分组。"""
        task_id = payload.get("task_id")
        if not task_id:
            await self.send_json(
                {
                    "type": "error",
                    "code": "40002",
                    "message": "缺少 task_id 参数",
                    "timestamp": time.time(),
                }
            )
            return

        from Django_xm.common.realtime_events import _group_name

        group = _group_name("task", task_id)
        if group in self.task_groups:
            await self.channel_layer.group_discard(group, self.channel_name)
            self.task_groups.discard(group)

        await self.send_json(
            {
                "type": "unsubscribed",
                "channel": group,
                "timestamp": time.time(),
            }
        )

    async def handle_ping(self, payload):
        """响应客户端心跳。"""
        await self.send_json(
            {
                "type": "pong",
                "timestamp": time.time(),
            }
        )

    async def handle_unknown(self, payload):
        """兜底：响应未知 action，避免静默失败。"""
        await self.send_json(
            {
                "type": "error",
                "code": "40001",
                "message": "未知动作",
                "timestamp": time.time(),
            }
        )

    async def handle_replay(self, payload):
        channel_type = payload.get("channel_type")
        channel_id = payload.get("channel_id")
        last_seq = payload.get("last_seq")
        # 统一底层修复：使用 EVENT_HISTORY_LIMIT（500）替代硬编码 100，
        # 确保回放完整，避免 session_created 等关键历史事件因 limit=100 被截断
        limit = min(int(payload.get("limit", EVENT_HISTORY_LIMIT)), EVENT_HISTORY_LIMIT)

        if channel_type not in ("user", "session", "task") or not channel_id:
            await self.send_json(
                {
                    "type": "error",
                    "code": "40003",
                    "message": "channel_type 必须是 user/session/task 且 channel_id 不能为空",
                    "timestamp": time.time(),
                }
            )
            return

        if channel_type == "session" and not await self._user_owns_session(channel_id):
            await self.send_json(
                {
                    "type": "error",
                    "code": "40401",
                    "message": "会话不存在或无权限",
                    "timestamp": time.time(),
                }
            )
            return

        if channel_type == "task" and not await self._user_owns_task(channel_id):
            await self.send_json(
                {
                    "type": "error",
                    "code": "40401",
                    "message": "任务不存在或无权限",
                    "timestamp": time.time(),
                }
            )
            return

        try:
            last_seq = int(last_seq) if last_seq is not None else None
            # limit 已在上方校验为 <= EVENT_HISTORY_LIMIT，此处不再二次限制
            history = await sync_to_async(get_event_history)(channel_type, channel_id, last_seq, limit)
            # 统一底层修复（刷新浏览器同步滞后根因）：
            # 改为分块发送，避免 500 条历史事件序列化后超过 WebSocket 1MB 限制
            await _send_replay_chunked(self, channel_type, channel_id, history)
        except Exception as e:
            logger.exception(f"[RealtimeSync] replay 失败: {channel_type}:{channel_id}")
            await self.send_json(
                {
                    "type": "error",
                    "code": "50002",
                    "message": f"replay 失败: {e!s}",
                    "timestamp": time.time(),
                }
            )
        finally:
            # 回放完成后清理 DB 连接
            await sync_to_async(close_old_connections)()

    # ==================== 广播事件接收 ====================

    async def broadcast_event(self, event):
        """接收 channel layer 的 group_send 事件，透传给客户端。

        透传的会话级事件类型（由 realtime_events.publish_event 发布）：
        - message_added: 新消息创建
        - message_updated: 消息内容更新
        - message_regenerated: 消息重新生成（版本归档 + 新版本切换）
        - messages_deleted: 消息删除
        - tool_call_*（pending/running/completed/failed/timeout 等）: 工具调用状态变更
        - approval_*（pending/processing/approved/rejected/timeout 等）: 审批状态变更
        - stream_completed / stream_finalized: 流式输出完成
        - session_created / session_updated / session_deleted: 会话级变更
        """
        wrapped = event.get("event")
        to_send = wrapped if wrapped is not None else {k: v for k, v in event.items() if k != "type"}
        try:
            await self.send_json(to_send)
            # 诊断日志：确认事件被转发到 WebSocket 客户端
            evt_type = to_send.get("type", "?")
            evt_seq = to_send.get("seq", "?")
            logger.debug(f"[RealtimeSync] broadcast_event 转发: user={self.user_id}, type={evt_type}, seq={evt_seq}")
        except Exception:
            logger.exception(
                f"[RealtimeSync] broadcast_event 发送失败: user={getattr(self, 'user_id', '?')}"
            )

    # ==================== 辅助方法 ====================

    @sync_to_async
    def _user_owns_session(self, session_id):
        from Django_xm.apps.chat.models import ChatSession

        return ChatSession.objects.filter(
            session_id=session_id,
            user_id=self.user_id,
            is_deleted=False,
        ).exists()

    @sync_to_async
    def _user_owns_task(self, task_id):
        """校验当前用户是否为指定 ResearchTask 的创建者。

        用于 task 通道订阅/回放权限校验，独立深度研究场景下
        确保用户只能订阅自己的任务事件。
        """
        from Django_xm.apps.research.services.cross_app import user_owns_research_task

        try:
            return user_owns_research_task(task_id, self.user)
        except Exception as e:
            logger.warning(f"[RealtimeSync] _user_owns_task 校验失败: task_id={task_id}, {e}")
            return False
