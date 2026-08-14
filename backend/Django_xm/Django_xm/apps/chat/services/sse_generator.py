"""chat 流式事件广播辅助（WebSocket 同步）。

执行与连接解耦后，chat agent 由 FastAPI 执行服务单协程运行，SSE 已不再承载
chat 流。此模块仅保留 ``_publish_stream_event``，将执行事件统一广播到 WebSocket
会话频道，供触发/非触发浏览器消费。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


_TOOL_EVENT_TYPES = frozenset({"tool", "tool_result", "tool_usage_dedup", "tool_usage_blocked"})


async def _publish_stream_event(
    event: dict[str, Any],
    session_id: str,
    message_id: int | None = None,
    content_state: dict[str, Any] | None = None,
) -> None:
    """将执行事件同步到 WebSocket 会话频道。

    - reasoning/sources/suggestions/context：直接广播 STREAM_* 事件
    - chunk：累积 content，500ms 节流广播 STREAM_CONTENT_UPDATE
    - tool 系列事件：直接广播 TOOL_CALL_* 事件
    - 其他事件类型：静默跳过

    Args:
        event: 执行事件 dict
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
        from Django_xm.common.tool_call_lifecycle import ToolCallContext
        from Django_xm.common.tool_call_lifecycle import service as lifecycle_service

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

            # PENDING 补发防护（根因修复，替代 register 拒绝 + 非法转换双重 WARNING）：
            # finalize_tool_calls（审批中断补发 tool 事件）/ resume 流 new_tool_calls
            # 等路径补发的 tool 事件，其工具状态可能已被审批中间件/SAFE 审计推进到
            # waiting/running。此时再发布 PENDING 会被状态机判定非法（waiting → pending）。
            # 发布前查询 last_event_type，已推进则跳过发布、仅补全参数到 context，
            # 后续 waiting/running/completed 事件携带补全后的参数。
            if target_event_type == EventType.TOOL_CALL_PENDING:
                _existing_ctx = lifecycle_service.get_context(tool_call_id)
                _last_event = _existing_ctx.get("last_event_type") if _existing_ctx else None
                if _last_event and _last_event != EventType.TOOL_CALL_PENDING.value:
                    tool_parameters_ready = tool_data.get("parameters") or tool_data.get("args")
                    if isinstance(tool_parameters_ready, dict) and tool_parameters_ready:
                        lifecycle_service.bind_parameters(tool_call_id, tool_parameters_ready)
                    logger.debug(
                        f"[Sync] PENDING 补发跳过（状态已推进）: tool_call_id={tool_call_id}, "
                        f"last_event_type={_last_event}, event_type_str={event_type_str}"
                    )
                    return

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
                parameters=(
                    tool_parameters
                    if tool_parameters and isinstance(tool_parameters, dict) and tool_parameters
                    else None
                ),
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
