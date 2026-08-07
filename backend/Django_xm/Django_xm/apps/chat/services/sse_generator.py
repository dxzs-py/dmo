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
                    logger.warning(
                        f"广播 STREAM_CONTENT_UPDATE 失败: event_type=chunk, "
                        f"session_id={session_id}, message_id={message_id}, error={e}"
                    )
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
            # 根据 lifecycle_event 字段确定目标事件类型（P-BE-1 根因修复）：
            # stream_chunk_processors._handle_tool_message_chunk 在 tool_info 中标记
            # lifecycle_event，指明具体的工具终态（timeout/rejected/failed/completed），
            # 而非简单判断 tool_result → COMPLETED。
            # tool 事件（非 tool_result）默认为 PENDING。
            lifecycle_event = tool_data.get("lifecycle_event")
            if lifecycle_event:
                target_event_type = EventType(lifecycle_event)
            elif event_type_str == "tool_result":
                target_event_type = EventType.TOOL_CALL_COMPLETED
            else:
                target_event_type = EventType.TOOL_CALL_PENDING

            # 确保上下文已注册（幂等，重复调用无副作用）
            # 补全 message_id 和 parameters（P-BE-3 修复）：
            # 前端通过 message_id 路由事件到正确消息，parameters 用于工具卡片参数回显。
            # parameters 兼容 args 字段：PENDING 指纹去重（Task 1）依赖 parameters 稳定哈希，
            # 若 tool 事件仅携带 args 会导致指纹退化为 no_batch 而失去参数维度区分。
            # 传入 event_type 以启用 register 的 last_event_type 防护（PENDING 状态已推进时拒绝）。
            lifecycle_service.register(
                ToolCallContext(
                    tool_call_id=tool_call_id,
                    tool_name=tool_data.get("name", ""),
                    module=EventSource.CHAT,
                    module_id=session_id,
                    message_id=str(message_id) if message_id else "",
                    parameters=tool_data.get("parameters") or tool_data.get("args") or {},
                ),
                event_type=target_event_type,
            )

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
            logger.warning(
                f"ToolCallLifecycleService 发布工具事件失败: "
                f"event_type={event_type_str}, tool_call_id={tool_call_id}, error={e}"
            )
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
                # 深度研究模式：chat SSE 因审批中断结束，广播 stream_interrupted，
                # 前端 handleStreamInterrupted 据此设置 researchTaskId + INTERRUPTED 状态，
                # 使非触发浏览器也能显示"研究进行中 + 查看详情"卡片。
                "interrupted": EventType.STREAM_INTERRUPTED,
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
            logger.warning(
                f"广播事件失败: event_type={event_type_str}, "
                f"session_id={session_id}, message_id={message_id}, error={e}"
            )


async def _finalize_stream_content(
    ctx: ChatStreamContext,
    current_content: str,
    tool_calls_map: dict[str, Any] | None = None,
) -> None:
    """流结束后持久化 assistant 消息内容与工具调用终态到数据库。

    复用 ``stream_persistence.persist_stream_result``（唯一持久化入口，不新增重复逻辑）：
    - tool_calls 增量合并（existing 优先，保留 approval 等审批中间态字段；
      流式累积的 completed/failed/timeout 终态 + result 写入新条目，P-FE-7 修复）
    - content 仅在新内容更长时覆盖
    - 广播 MESSAGE_UPDATED，通知非触发浏览器拉取完整 tool_calls

    参考项目在 finally 块中执行此操作，确保无论流如何结束，
    内容都能被保存到数据库。

    Args:
        ctx: 流式上下文
        current_content: 累积的完整 assistant 回复内容
        tool_calls_map: 工具调用映射表（含终态 state/status/result/error）
    """
    if not ctx.assistant_message_id:
        return

    session_id = ctx.data.get("session_id", "")
    if not session_id:
        return

    from asgiref.sync import sync_to_async
    from django.apps import apps

    from Django_xm.apps.chat.services.stream_persistence import persist_stream_result

    ChatMessage = apps.get_model("chat", "ChatMessage")

    try:
        # 复用现有持久化入口：工具终态（completed/failed/timeout + result）
        # 由后端在流结束时可靠落库，不再依赖前端 syncLastMessageToBackend 回写
        await persist_stream_result(
            session_id=session_id,
            user_id=None,
            content=current_content,
            tool_calls_map=tool_calls_map or {},
            message_id=str(ctx.assistant_message_id),
        )

        # persist_stream_result 不处理 is_streaming，流结束须标记为非流式
        @sync_to_async
        def _mark_not_streaming():
            msg = ChatMessage.objects.filter(id=ctx.assistant_message_id).first()
            if msg and msg.is_streaming:
                msg.is_streaming = False
                msg.save(update_fields=["is_streaming"])

        await _mark_not_streaming()

        logger.info(
            f"[SSE Finalize] 持久化 assistant 消息: id={ctx.assistant_message_id}, "
            f"content_len={len(current_content)}, tool_calls_count={len(tool_calls_map or {})}"
        )
    except Exception as e:
        logger.warning(f"[SSE Finalize] 持久化内容失败: {e}", exc_info=True)


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

                # message_id 必须放在 payload 顶层（与节流广播格式一致），
                # 否则前端 handleStreamEvent 解构 messageId 失败，最终完整 content 被丢弃
                await publish_event(
                    EventType.STREAM_CONTENT_UPDATE,
                    {
                        "source": EventSource.CHAT,
                        "source_id": session_id,
                        "message_id": str(ctx.assistant_message_id) if ctx.assistant_message_id else None,
                        "data": {
                            "content": final_content,
                        },
                    },
                    session_id=session_id,
                )
            except Exception as e:
                logger.warning(
                    f"finally 块广播 STREAM_CONTENT_UPDATE 失败: "
                    f"session_id={session_id}, error={e}"
                )

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
        # tool 事件创建条目；tool_result 事件更新终态（result/error/state/status），
        # 确保流结束时 tool_calls_map 携带 completed/failed/timeout 终态（P-FE-7 持久化链修复）
        if event_type in ("tool", "tool_result") and isinstance(event.get("data"), dict):
            tc_data = event["data"]
            tc_id = tc_data.get("id") or ""
            if tc_id:
                entry = tool_calls_map.get(tc_id)
                if entry is None:
                    entry = {
                        "id": tc_id,
                        "name": tc_data.get("name", ""),
                        "parameters": tc_data.get("parameters", {}),
                        "state": tc_data.get("state", ""),
                        "status": tc_data.get("status", ""),
                    }
                    tool_calls_map[tc_id] = entry
                if tc_data.get("name"):
                    entry["name"] = tc_data["name"]
                if isinstance(tc_data.get("parameters"), dict) and tc_data["parameters"]:
                    entry["parameters"] = tc_data["parameters"]
                if tc_data.get("state"):
                    entry["state"] = tc_data["state"]
                if tc_data.get("status"):
                    entry["status"] = tc_data["status"]
                if tc_data.get("result") is not None:
                    entry["result"] = tc_data["result"]
                if tc_data.get("error") is not None:
                    entry["error"] = tc_data["error"]

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
