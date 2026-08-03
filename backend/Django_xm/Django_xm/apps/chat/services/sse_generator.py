"""SSE 流式聊天生成器（异步版本）

将原 ``views_chat.py`` 中 ``ChatStreamView.post().generate()`` 闭包拆分为
职责单一的模块（Task 23.1 重构）：

- ``generate_chat_stream``：异步 SSE 生成器，运行在 ASGI 原生事件循环中
- ``_publish_stream_event``：WebSocket 事件同步辅助函数
- ``_finalize_stream_content``：流结束后持久化内容到数据库

关键修复（对比参考项目）：
- 移除 ``asyncio.new_event_loop()``，使用 ASGI 原生事件循环，
  解决 Django ORM / async checkpointer 连接池绑定到错误事件循环的问题
- 工具事件发布统一通过 ToolCallLifecycleService，主 SSE 流不推送工具事件至 SSE，
  仅通过 WebSocket 广播；恢复 SSE 流通过 transition_async 统一发布
- 5 分钟全局超时保护
- 流结束后持久化 assistant 消息内容到数据库
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest

from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.sse_utils import sse_error_event

logger = logging.getLogger(__name__)


# ============== 流式上下文 ==============


@dataclass
class ChatStreamContext:
    """SSE 流式聊天上下文，封装原 generate() 闭包的共享状态。"""

    request: HttpRequest
    data: dict[str, Any]
    original_attachment_ids: list[str]
    pending_progress: list[dict[str, Any]]
    # 流式消息 ID（由 ChatStreamView.post() 在流开始前创建）
    assistant_message_id: int | None = None
    user_message_id: int | None = None
    # 共享状态：async generator 被强制关闭时，finally 块兜底刷新 pending_content
    stream_state: dict[str, str] = field(default_factory=lambda: {"pending_content": ""})


# ============== 辅助函数 ==============


_TOOL_EVENT_TYPES = frozenset({"tool", "tool_result", "tool_usage_dedup", "tool_usage_blocked"})


async def _publish_stream_event(
    event: dict[str, Any],
    session_id: str,
    message_id: int | None = None,
    content_state: dict[str, Any] | None = None,
) -> None:
    """将 SSE 事件同步到 WebSocket 会话频道。

    - reasoning/sources/suggestions/context：直接广播 STREAM_* 事件
    - chunk：累积 content，500ms 节流广播 STREAM_CONTENT_UPDATE
    - tool 系列事件：直接广播 TOOL_CALL_* 事件
    - 其他事件类型：静默跳过

    Args:
        event: SSE 事件 dict
        session_id: 会话 ID
        message_id: 消息 ID
        content_state: chunk 节流累积状态
    """
    if not isinstance(event, dict) or not session_id:
        return

    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    event_type_str = event.get("type", "")

    # ── chunk 事件：累积 + 节流广播 ──
    if event_type_str == "chunk":
        if content_state is not None:
            content_state["content"] += event.get("content", "")
            now = time.monotonic()
            if now - content_state.get("last_broadcast", 0) >= 0.5:
                content_state["last_broadcast"] = now
                try:
                    await publish_event(
                        EventType.STREAM_CONTENT_UPDATE,
                        {
                            "source": EventSource.CHAT,
                            "source_id": session_id,
                            "message_id": str(message_id) if message_id else None,
                            "data": {
                                "content": content_state["content"],
                            },
                        },
                        session_id=session_id,
                    )
                except Exception as e:
                    logger.debug(f"广播 STREAM_CONTENT_UPDATE 失败: {e}")
        return

    # ── tool 系列事件：通过 ToolCallLifecycleService 统一发布 ──
    # 先 register 确保上下文存在（幂等，参考项目 _publish_tool_lifecycle_event），
    # 再 transition_async 发布事件。若上下文缺失直接 transition 会被静默丢弃，
    # 导致非触发浏览器收不到工具结果。
    if event_type_str in _TOOL_EVENT_TYPES:
        from Django_xm.common.tool_call_lifecycle import service as lifecycle_service, ToolCallContext

        tool_data = event.get("data", {})
        tool_call_id = tool_data.get("id") or tool_data.get("tool_call_id", "")
        if not tool_call_id:
            return

        try:
            target_event_type = (
                EventType.TOOL_CALL_OUTPUT_READY if event_type_str == "tool_result"
                else EventType.TOOL_CALL_INPUT_READY
            )

            # 确保上下文已注册（幂等，重复调用无副作用）
            lifecycle_service.register(ToolCallContext(
                tool_call_id=tool_call_id,
                tool_name=tool_data.get("name", ""),
                module=EventSource.CHAT,
                module_id=session_id,
            ))

            tool_result = tool_data.get("result")
            tool_error = tool_data.get("error")
            tool_parameters = tool_data.get("parameters") or tool_data.get("args")

            await lifecycle_service.transition_async(
                tool_call_id,
                target_event_type,
                result=tool_result,
                error=tool_error,
                parameters=tool_parameters if tool_parameters and isinstance(tool_parameters, dict) and tool_parameters else None,
            )
        except Exception as e:
            logger.debug(f"ToolCallLifecycleService 发布工具事件失败: tool={tool_call_id}, error={e}")
        return

    # ── reasoning / sources / suggestions / context / deep_research / approval ──
    if event_type_str in (
        "reasoning", "sources", "suggestions", "context",
        "deep_research", "approval", "interrupted",
        "model_fallback", "research_task_id",
    ):
        try:
            stream_event_type_map = {
                "reasoning": EventType.STREAM_REASONING,
                "sources": EventType.STREAM_SOURCES,
                "suggestions": EventType.STREAM_SUGGESTIONS,
                "context": EventType.STREAM_CONTEXT,
            }
            evt_type = stream_event_type_map.get(event_type_str, EventType.STREAM_EVENT)
            await publish_event(
                evt_type,
                {
                    "source": EventSource.CHAT,
                    "source_id": session_id,
                    "message_id": str(message_id) if message_id else None,
                    "data": event.get("data", event),
                },
                session_id=session_id,
            )
        except Exception as e:
            logger.debug(f"广播 {event_type_str} 事件失败: {e}")


async def _finalize_stream_content(
    ctx: ChatStreamContext,
    current_content: str,
    tool_calls_map: dict[str, Any] | None = None,
) -> None:
    """流结束后持久化 assistant 消息内容到数据库。

    参考项目在 finally 块中执行此操作，确保无论流如何结束，
    内容都能被保存到数据库。

    Args:
        ctx: 流式上下文
        current_content: 累积的完整 assistant 回复内容
        tool_calls_map: 工具调用映射表（用于持久化 tool_calls 数据）
    """
    if not ctx.assistant_message_id or not current_content.strip():
        return

    from asgiref.sync import sync_to_async
    from django.apps import apps

    ChatMessage = apps.get_model("chat", "ChatMessage")

    try:

        @sync_to_async
        def _save():
            msg = ChatMessage.objects.filter(id=ctx.assistant_message_id).first()
            if msg:
                msg.content = current_content
                msg.is_streaming = False
                if tool_calls_map:
                    # 持久化 tool_calls 数据
                    persisted_tool_calls = []
                    for tc_key, tc_info in tool_calls_map.items():
                        tc_data = {
                            "id": tc_info.get("id") or tc_key,
                            "name": tc_info.get("name", ""),
                            "args": tc_info.get("parameters", {}),
                        }
                        persisted_tool_calls.append(tc_data)
                    msg.tool_calls = persisted_tool_calls
                msg.save(update_fields=["content", "is_streaming", "tool_calls"])

        await _save()
        logger.info(
            f"[SSE Finalize] 持久化 assistant 消息: id={ctx.assistant_message_id}, "
            f"content_len={len(current_content)}"
        )
    except Exception as e:
        logger.warning(f"[SSE Finalize] 持久化内容失败: {e}")


# ============== 主生成器 ==============


async def generate_chat_stream(ctx: ChatStreamContext) -> AsyncGenerator[str, None]:
    """异步 SSE 流式聊天生成器（运行在 ASGI 原生事件循环中）。

    取代原 ``ChatStreamView.post().generate()`` 闭包，遵循 Init → Process → Cleanup
    三阶段模式，使用 ASGI 原生事件循环而非创建独立事件循环。

    关键设计：
    - 工具事件（tool/tool_result/tool_usage_dedup/tool_usage_blocked）
      不通过 SSE 推送，仅通过 WebSocket 同步到其他浏览器
    - 5 分钟全局超时保护
    - 流结束后持久化 assistant 消息内容到数据库

    Args:
        ctx: 流式上下文

    Yields:
        SSE 格式字符串（``"data: ...\\n\\n"``）
    """
    from .chat_service import ChatService

    # 共享状态注入到 data，供 ChatService 内部写入 pending_content
    ctx.data["_stream_state"] = ctx.stream_state

    stream_start_time = time.monotonic()
    STREAM_MAX_DURATION = 300  # 5 分钟全局超时
    # content_state 同时追踪累积内容和节流状态
    content_state: dict[str, Any] = {"content": "", "last_broadcast": 0.0}
    tool_calls_map: dict[str, Any] = {}
    session_id = ctx.data.get("session_id", "")

    # ── Phase 1: Init ──
    # 发送附件 ID 和预处理进度事件
    if ctx.original_attachment_ids:
        yield f"data: {json.dumps({'type': 'attachment_ids', 'data': ctx.original_attachment_ids}, ensure_ascii=False)}\n\n"

    for evt in ctx.pending_progress:
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
    ctx.pending_progress.clear()

    try:
        # ── Phase 2: Process ──
        chat_service = ChatService(
            user_id=ctx.request.user.id if ctx.request.user.is_authenticated else None,
            thread_id=session_id,
        )

        # 使用 sse_async_heartbeat_generator 包装，支持心跳保活
        from Django_xm.common.sse_utils import sse_async_heartbeat_generator

        async for sse_line in sse_async_heartbeat_generator(
            _stream_events(chat_service, ctx, content_state, tool_calls_map, stream_start_time, STREAM_MAX_DURATION)
        ):
            yield sse_line

    except Exception as e:
        logger.exception("流式处理出错")
        from Django_xm.apps.ai_engine.services.exceptions import classify_exception

        classified = classify_exception(e)
        yield sse_error_event(
            code=str(int(ErrorCode.SERVER_ERROR)),
            message=classified.user_message,
        )
    finally:
        # ── Phase 3: Cleanup ──
        # 累积内容 = content_state 中的内容 + pending_content 兜底
        final_content = content_state.get("content", "")
        pending_content = ctx.stream_state.get("pending_content", "")
        if pending_content:
            final_content += pending_content
            yield f"data: {json.dumps({'type': 'chunk', 'content': pending_content}, ensure_ascii=False)}\n\n"

        # 兜底广播最后一次 content_update
        if final_content:
            try:
                from Django_xm.common.event_schema import EventSource, EventType
                from Django_xm.common.realtime_events import publish_event

                await publish_event(
                    EventType.STREAM_CONTENT_UPDATE,
                    {
                        "source": EventSource.CHAT,
                        "source_id": session_id,
                        "data": {
                            "message_id": str(ctx.assistant_message_id) if ctx.assistant_message_id else None,
                            "content": final_content,
                        },
                    },
                    session_id=session_id,
                )
            except Exception as e:
                logger.debug(f"finally 块广播 content_update 失败: {e}")

        # 持久化 assistant 消息内容到数据库
        await _finalize_stream_content(ctx, final_content, tool_calls_map)

        # 释放异步 Checkpointer 连接池
        try:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

            await release_async_checkpointer()
        except Exception as e:
            logger.warning(f"[SSE Cleanup] Checkpointer 连接释放失败: {e}", exc_info=True)

        yield "data: [DONE]\n\n"


async def _stream_events(
    chat_service,
    ctx: ChatStreamContext,
    content_state: dict[str, Any],
    tool_calls_map: dict[str, Any],
    stream_start_time: float,
    STREAM_MAX_DURATION: float,
) -> AsyncGenerator[str, None]:
    """内部异步生成器：迭代 ChatService 流式事件，过滤 + 同步。

    被 ``sse_async_heartbeat_generator`` 包装以提供心跳保活支持。

    Yields:
        SSE 格式字符串
    """
    session_id = ctx.data.get("session_id", "")

    async for event in chat_service.process_stream_chat_request(ctx.data):
        # 全局超时检查
        elapsed = time.monotonic() - stream_start_time
        if elapsed > STREAM_MAX_DURATION:
            logger.warning(f"[ChatStream] 全局超时 ({STREAM_MAX_DURATION}s)，终止流式输出")
            yield sse_error_event(
                code=str(int(ErrorCode.SERVER_ERROR)),
                message=f"响应超时（已等待 {int(elapsed)}s），请稍后重试",
            )
            break

        if not isinstance(event, dict):
            continue

        event_type = event.get("type", "")

        # ── error 事件 ──
        if event_type == "error":
            yield sse_error_event(
                code=str(int(ErrorCode.SERVER_ERROR)),
                message=event.get("message", "处理出错"),
            )
            continue

        # ── 收集 tool_calls_map 用于持久化 ──
        if event_type == "tool" and isinstance(event.get("data"), dict):
            tc_data = event["data"]
            tc_id = tc_data.get("id") or ""
            if tc_id and tc_id not in tool_calls_map:
                tool_calls_map[tc_id] = {
                    "id": tc_id,
                    "name": tc_data.get("name", ""),
                    "parameters": tc_data.get("parameters", {}),
                    "state": tc_data.get("state", ""),
                    "status": tc_data.get("status", ""),
                }

        # ── 工具事件：仅 WebSocket，不通过 SSE 推送 ──
        is_tool_event = event_type in _TOOL_EVENT_TYPES

        if not is_tool_event:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # ── WebSocket 同步（所有事件，含 chunk 节流广播） ──
        await _publish_stream_event(
            event,
            session_id,
            message_id=ctx.assistant_message_id,
            content_state=content_state,
        )
