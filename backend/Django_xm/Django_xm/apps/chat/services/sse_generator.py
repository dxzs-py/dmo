"""chat 流式事件广播辅助（WebSocket 同步）。

执行与连接解耦后，chat agent 由 FastAPI 执行服务单协程运行，SSE 已不再承载
chat 流。此模块仅保留 ``publish_stream_event``，将执行事件统一广播到 WebSocket
会话频道，供触发/非触发浏览器消费。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


_TOOL_EVENT_TYPES = frozenset({"tool", "tool_result", "tool_usage_dedup", "tool_usage_blocked"})


def find_content_overlap(existing: str, new_chunk: str) -> int:
    """计算 new_chunk 前缀与 existing 后缀的最长重叠长度（尾部重叠去重核心判定）。

    业务等待挂起（wait_for_subagent）时挂起前 content 已落库（含流式中途的
    尾部字符），恢复轮 LLM 从 checkpoint 继续生成时会重新输出与该尾部重叠的
    内容。若直接 ``existing += new_chunk``，会出现「三个三个」类重复。

    规则：取 new_chunk 前缀与 existing 后缀的最大重叠（最长重叠后缀/前缀），
    无重叠返回 0。此函数为全项目唯一权威实现，stream_persistence 等
    持久化链路通过 import 复用，禁止复制实现。

    Args:
        existing: 已有累计正文
        new_chunk: 新到达的正文增量

    Returns:
        int: 最长重叠长度（0 表示无重叠）
    """
    if not new_chunk or not existing:
        return 0
    max_overlap = min(len(existing), len(new_chunk))
    for i in range(max_overlap, 0, -1):
        if existing[-i:] == new_chunk[:i]:
            return i
    return 0


def _merge_content_with_overlap(existing: str, new_chunk: str) -> str:
    """累积正文并做尾部重叠去重（恢复轮拼接防重复）。

    重叠判定（find_content_overlap）与拼接逻辑为全项目唯一实现：
    - 取 new_chunk 前缀与 existing 后缀的最大重叠，拼接时去掉重叠部分；
    - 无重叠时退化为普通追加；空 chunk 幂等返回 existing。
    """
    if not new_chunk:
        return existing
    if not existing:
        return new_chunk
    overlap = find_content_overlap(existing, new_chunk)
    return existing + new_chunk[overlap:]


async def publish_stream_event(
    event: dict[str, Any],
    session_id: str,
    message_id: int | None = None,
    content_state: dict[str, Any] | None = None,
    subagent_contents: dict[str, dict[str, str]] | None = None,
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
        subagent_contents: 子代理图层正文累计（{subagent_thread_id: {content, reasoning_content}}，
            按 subagent_thread_id 键累计，spec MODIFIED）。子代理工具 position 采集依据；
            None 时子代理工具不注入 position（历史/兜底路径）。
    """
    if not isinstance(event, dict) or not session_id:
        return

    from Django_xm.common.event_schema import EventSource, EventType
    from Django_xm.common.realtime_events import publish_event

    event_type_str = event.get("type", "")

    # ── chunk 事件：累积 + 节流广播 ──
    if event_type_str == "chunk":
        if content_state is not None:
            content_state["content"] = _merge_content_with_overlap(
                content_state.get("content", ""), event.get("content", "")
            )
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
                    # position 补全（与正常发布路径一致）：PENDING 被跳过时不执行
                    # 下方的 bind_position，会导致 spawn2/3、wait_for_subagent 等
                    # 同批/后续工具在事件链路缺 position、前端切段错位（正文被插到
                    # 工具卡之间）。position 独立于事件发布，直接按当前图层正文长度
                    # 绑定（bind_position keep_existing 幂等，首次值永久不变）。
                    _sub_thread_id = tool_data.get("subagent_thread_id") or ""
                    if _sub_thread_id:
                        _pos = len((subagent_contents or {}).get(_sub_thread_id) or {}).get("content") or ""
                    elif content_state is not None:
                        _pos = len(content_state.get("content") or "")
                    else:
                        _pos = None
                    if _pos is not None:
                        try:
                            lifecycle_service.bind_position(tool_call_id, _pos)
                        except Exception:
                            logger.warning(
                                f"绑定 position 失败: tool_call_id={tool_call_id}, position={_pos}"
                            )
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
            # 子 agent 嵌套层级字段（spec D1）：tool_data 可能携带
            # parent_tool_call_id / depth / agent_name / description / subagent_thread_id
            # （SubAgentToolEventMiddleware 转发的子代理工具事件），透传注册
            # （agent_path 不再注册——spec REMOVED：事件路由与累计键均已收敛为
            # subagent_thread_id，agent_path 不进入 TCC 与事件 payload）。
            _sub_depth_raw = tool_data.get("depth", 0)
            if not isinstance(_sub_depth_raw, int) or _sub_depth_raw < 0:
                _sub_depth_raw = 0
            lifecycle_service.register(
                ToolCallContext(
                    tool_call_id=tool_call_id,
                    tool_name=tool_data.get("name", ""),
                    module=EventSource.CHAT,
                    module_id=session_id,
                    message_id=str(message_id) if message_id else "",
                    parameters=tool_data.get("parameters") or tool_data.get("args") or {},
                    parent_tool_call_id=tool_data.get("parent_tool_call_id") or "",
                    depth=_sub_depth_raw,
                    agent_name=tool_data.get("agent_name") or "",
                    description=tool_data.get("description") or "",
                ),
                event_type=target_event_type,
            )

            # position 采集（Task 2.1，按图层局部化，单一权威来源）：
            # - 子代理（subagent_thread_id 非空，spec D1）：position = len(该子代理图层已累计正文 content)
            # - 主 agent：position = len(content_state["content"])（已输出 content 长度）
            # 经 bind_position 写入 context（仅补全空字段），后续状态事件不覆盖。
            _position: int | None = None
            _sub_thread_id = tool_data.get("subagent_thread_id") or ""
            if _sub_thread_id:
                _sub_entry = (subagent_contents or {}).get(_sub_thread_id) or {}
                _position = len(_sub_entry.get("content") or "")
            elif content_state is not None:
                _position = len(content_state.get("content") or "")
            if _position is not None:
                try:
                    lifecycle_service.bind_position(tool_call_id, _position)
                except Exception:
                    logger.warning(
                        f"绑定 position 失败: tool_call_id={tool_call_id}, position={_position}"
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
