"""run_stream_loop — 核心流式循环（单一底层模块）

chat_service 共用的唯一 astream 循环实现。
保证以下行为在普通模式和深度思考模式下完全一致：

- astream stream_mode=["messages", "updates"]
- updates 模式：审批中断检测（extract_interrupt_ids + parse_approval_interrupt）
- messages 模式：process_stream_chunk 调用 + 事件分发
- chunk 累积到 ctx.current_message_content
- asyncio.sleep(0.01) 让出事件循环

不包含：韧性重试、超时（由 AgentExecutor 包装，位于 agent_hub/services/agent_executor.py）
不包含：审批中断收尾、finalize、建议生成（由 interrupt.py / finalizer.py 处理）
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

from Django_xm.apps.chat.services.stream_helpers import (
    _extract_tool_params,
    _fix_groq_tool_call,
    extract_interrupt_ids,
    parse_approval_interrupt,
    process_stream_chunk,
)
from Django_xm.apps.chat.utils import _lcp_len
from Django_xm.apps.tools.base import is_approval_interrupt
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

from .context import StreamContext
from .strategy import BaseStreamStrategy

logger = logging.getLogger(__name__)


def _publish_input_ready_events(message: Any, *, session_id: str) -> None:
    """检测 AIMessage.tool_calls 并发布 INPUT_READY 事件。

    在 process_stream_chunk 处理 AIMessage 之前调用，确保非触发浏览器
    通过 WebSocket 收到 INPUT_READY 事件（含输入参数）。

    ⚠️ 关键修复（与 tool_event_extractor.py 一致）：
    AIMessageChunk 是 AIMessage 的子类，其 ``.tool_calls`` 属性内部使用
    ``parse_partial_json`` 解析 args 字符串，对不完整 JSON 可能返回非空但
    残缺的 dict（如 ``'{"file_path": "/san'`` 被解析为 ``{"file_path": "/san"}``）。
    若直接使用 ``.tool_calls`` 发射 INPUT_READY，会携带不完整参数过早发射，
    导致跨浏览器同步展示残缺参数。

    修复：对 AIMessageChunk，直接从 ``tool_call_chunks`` 读取原始 args 字符串，
    用严格 ``json.loads`` 解析（仅完整 JSON 才成功），避免 parse_partial_json
    的宽容解析。完整 AIMessage 的 ``.tool_calls`` 的 args 已是 dict，可直接使用。

    Args:
        message: chunk 中的消息对象（AIMessage / AIMessageChunk 或 tuple 解构后的 message）
        session_id: 会话 ID（用于 module_id 字段）
    """
    if not isinstance(message, AIMessage):
        return

    # AIMessageChunk：从 tool_call_chunks 用严格 JSON 解析 args
    if isinstance(message, AIMessageChunk):
        tccs = getattr(message, "tool_call_chunks", None) or []
        for tcc in tccs:
            if isinstance(tcc, dict):
                tc_id = tcc.get("id") or ""
                tc_name = tcc.get("name") or ""
                tc_args_str = tcc.get("args") or ""
            else:
                tc_id = getattr(tcc, "id", "") or ""
                tc_name = getattr(tcc, "name", "") or ""
                tc_args_str = getattr(tcc, "args", "") or ""
            if not tc_id:
                continue
            # 严格 JSON 解析：仅完整 JSON 才发布 INPUT_READY。
            # args 为空或不完整时跳过，等后续 chunk 补全参数。
            if not tc_args_str or not tc_args_str.strip():
                continue
            try:
                parsed_args = json.loads(tc_args_str)
            except (json.JSONDecodeError, ValueError):
                continue  # args 不完整，等后续 chunk 补全
            if isinstance(parsed_args, dict) and parsed_args:
                parameters = parsed_args
            elif isinstance(parsed_args, list) and parsed_args:
                parameters = {"items": parsed_args}
            else:
                continue
            try:
                service.register(
                    ToolCallContext(
                        tool_call_id=tc_id,
                        tool_name=tc_name,
                        module=EventSource.CHAT,
                        module_id=session_id,
                        parameters=parameters,
                    )
                )
                service.transition(
                    tc_id,
                    EventType.TOOL_CALL_INPUT_READY,
                    parameters=parameters or None,
                )
            except Exception as e:
                logger.warning(f"发布 INPUT_READY 事件失败: tool_call_id={tc_id}, err={e}")
        return

    # 完整 AIMessage（非 chunk）：tool_calls 的 args 已是完整 dict，直接使用
    tool_calls = getattr(message, "tool_calls", []) or []
    if not tool_calls:
        return

    for raw_tool_call in tool_calls:
        if not isinstance(raw_tool_call, dict):
            continue
        tool_call = _fix_groq_tool_call(raw_tool_call)
        tool_call_id = tool_call.get("id") or ""
        tool_name = tool_call.get("name") or ""
        if not tool_call_id:
            continue

        parameters = _extract_tool_params(tool_call)
        try:
            service.register(
                ToolCallContext(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    module=EventSource.CHAT,
                    module_id=session_id,
                    parameters=parameters,
                )
            )
            service.transition(
                tool_call_id,
                EventType.TOOL_CALL_INPUT_READY,
                parameters=parameters or None,
            )
        except Exception as e:
            logger.warning(f"发布 INPUT_READY 事件失败: tool_call_id={tool_call_id}, err={e}")


async def run_stream_loop(
    agent,
    graph_input: dict,
    config: dict,
    ctx: StreamContext,
    strategy: BaseStreamStrategy,
    data: dict,
) -> AsyncGenerator[dict, None]:
    """核心流式循环

    Args:
        agent: 已创建的 agent（含 graph 属性）
        graph_input: graph 输入（messages 列表）
        config: graph 配置（含 callbacks、recursion_limit 等）
        ctx: 流式可变状态
        strategy: 模式策略（Normal / DeepThinking）
        data: 请求数据
    """
    # 策略钩子：循环开始前的事件（深度思考发送"正在深度思考中..."）
    async for event in strategy.on_loop_start(ctx, data):
        yield event

    async for chunk in agent.graph.astream(graph_input, config=config, stream_mode=["messages", "updates"]):
        # 多 stream mode 下 chunk 是 (mode_name, data) 元组
        if isinstance(chunk, tuple) and len(chunk) == 2:
            mode_name, mode_data = chunk
        else:
            mode_name, mode_data = "messages", chunk

        # ── updates 模式：审批中断检测 ──
        if mode_name == "updates":
            async for event in _handle_updates_chunk(mode_data, ctx, data):
                yield event
            continue

        # ── messages 模式：处理消息流 ──
        message = mode_data[0] if isinstance(mode_data, tuple) and len(mode_data) == 2 else mode_data
        ctx.all_messages.append(message)

        # 在 process_stream_chunk 之前发布 INPUT_READY 事件（非触发浏览器可见）
        # 仅对 AIMessage.tool_calls 发布；其他消息类型函数内部会跳过
        _publish_input_ready_events(
            message,
            session_id=data.get("session_id", ""),
        )

        try:
            for event in process_stream_chunk(
                mode_data,
                ctx.tool_calls_map,
                ctx.current_message_content,
                tool_call_count=ctx.tool_call_count,
                lcp_func=_lcp_len,
                accumulated_reasoning=ctx.accumulated_reasoning,
                tool_args_accumulator=ctx.tool_args_accumulator,
                mode=data.get("mode", "agent"),
                enable_deep_thinking=strategy.enable_deep_thinking,
            ):
                # chunk 事件：累积内容
                if event.get("type") == "chunk":
                    ctx.current_message_content += event.get("content", "")

                # 策略钩子：处理 tool_usage_dedup/blocked/reasoning 等特殊事件
                replacement = strategy.handle_special_event(event, ctx)
                if replacement is not None:
                    for replacement_event in replacement:
                        yield replacement_event
                    continue

                yield event
        except Exception as chunk_err:
            logger.warning(f"处理流式 chunk 失败: {chunk_err}")
            continue

        await asyncio.sleep(0.01)


async def _handle_updates_chunk(
    mode_data: Any,
    ctx: StreamContext,
    data: dict,
) -> AsyncGenerator[dict, None]:
    """处理 updates stream mode chunk（审批中断检测）

    检测 __interrupt__ 事件，解析审批请求，更新 ctx.interrupt_info。
    """
    if not (isinstance(mode_data, dict) and "__interrupt__" in mode_data):
        return

    interrupts = mode_data["__interrupt__"]
    if not interrupts:
        return

    for intr in interrupts:
        interrupt_value, graph_interrupt_id, langgraph_resume_id = extract_interrupt_ids(intr)

        if not is_approval_interrupt(interrupt_value):
            continue

        approval_data_list = parse_approval_interrupt(
            interrupt_value,
            graph_interrupt_id=graph_interrupt_id,
            langgraph_resume_id=langgraph_resume_id,
            tool_calls_map=ctx.tool_calls_map,
            tool_args_accumulator=ctx.tool_args_accumulator,
            used_tool_call_ids=ctx.used_tool_call_ids,
        )

        for approval_data in approval_data_list:
            tool_name = approval_data.get("tool_name", "unknown")
            action = approval_data.get("action", "confirm")
            logger.info(
                f"approval interrupt: tool={tool_name}, "
                f"action={action}, danger={approval_data.get('danger_level', 'medium')}, "
                f"interrupt_id={approval_data.get('interrupt_id')}, "
                f"graph_interrupt_id={graph_interrupt_id}, "
                f"langgraph_resume_id={langgraph_resume_id}"
            )
            # 标记发生了审批中断（用第一个请求的信息）
            if ctx.interrupt_info is None:
                ctx.interrupt_info = {
                    "tool_name": tool_name,
                    "interrupt_id": approval_data.get("interrupt_id"),
                    "graph_interrupt_id": graph_interrupt_id,
                    "langgraph_resume_id": langgraph_resume_id,
                }
            yield {
                "type": "approval",
                "data": approval_data,
            }
