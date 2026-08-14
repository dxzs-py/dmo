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
from Django_xm.common.event_schema import EventSource
from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

from .context import StreamContext
from .strategy import BaseStreamStrategy

logger = logging.getLogger(__name__)


def _publish_input_ready_events(message: Any, *, session_id: str, seen_tool_call_ids: set) -> None:
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
        seen_tool_call_ids: 整条流中已发布 PENDING 的 tool_call_id 集合，用于去重
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
            # 语义完整性检查：所有 string 值均为空串 → LLM 流式占位，跳过
            # 避免发布 {"relative_path": "", "content": ""} 等空参数工具事件
            if isinstance(parsed_args, dict):
                string_values = [v for v in parsed_args.values() if isinstance(v, str)]
                if string_values and all(v == '' for v in string_values):
                    continue
            # 去重：同一流式生命周期中每个 tool_call_id 仅发布一次 PENDING
            if tc_id in seen_tool_call_ids:
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
                # PENDING 事件由 _publish_stream_event 统一发布（P-BE-1 根因修复）：
                # 原在此处 sync transition（fire-and-forget）立即设置 dedup_key，
                # 导致后续 _publish_stream_event 的 async transition_async 被 dedup 跳过，
                # 事件可能延迟或丢失。register 已注册上下文（含 parameters），
                # _publish_stream_event 处理 SSE tool 事件时会复用并 await 发布。
                seen_tool_call_ids.add(tc_id)
            except Exception as e:
                logger.warning(f"发布 PENDING 事件失败: tool_call_id={tc_id}, err={e}")
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
        # 去重：同一流式生命周期中每个 tool_call_id 仅发布一次 PENDING
        if tool_call_id in seen_tool_call_ids:
            continue
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
            # PENDING 事件由 _publish_stream_event 统一发布（P-BE-1 根因修复）：
            # 原在此处 sync transition（fire-and-forget）立即设置 dedup_key，
            # 导致后续 _publish_stream_event 的 async transition_async 被 dedup 跳过。
            # register 已注册上下文（含 parameters），_publish_stream_event 会复用。
            seen_tool_call_ids.add(tool_call_id)
        except Exception as e:
            logger.warning(f"发布 PENDING 事件失败: tool_call_id={tool_call_id}, err={e}")


async def run_stream_loop(
    agent,
    graph_input: dict,
    config: dict,
    ctx: StreamContext,
    strategy: BaseStreamStrategy,
    data: dict,
    interrupt_handler=None,
) -> AsyncGenerator[dict, None]:
    """核心流式循环（SSE 模式 / 单协程挂起模式）

    单协程挂起模式（interrupt_handler 非 None，执行服务使用）：
    - 外层 while 驱动 astream；
    - updates 检测到审批中断 → 创建审批 + 产出 approval 事件 → 调用
      interrupt_handler 挂起等待批次决策 → Command(resume=...) 重入 astream；
    - 审批挂起期间不产出事件（不推进超时计时），用户思考时间不惩罚 agent。

    SSE 模式（interrupt_handler 为 None，旧路径）：
    - 中断时产出 approval 事件后 astream 自然结束（保持原行为）。

    Args:
        agent: 已创建的 agent（含 graph 属性）
        graph_input: graph 输入（messages 列表）
        config: graph 配置（含 callbacks、recursion_limit 等）
        ctx: 流式可变状态
        strategy: 模式策略（Normal / DeepThinking）
        data: 请求数据
        interrupt_handler: 审批中断挂起回调，签名
            async fn(graph_interrupt_id, langgraph_resume_id) -> decisions dict；
            返回 {langgraph_resume_id: {tool_call_id: bool}}。为 None 时走 SSE 模式。
    """
    # 去重：整条流中每个 tool_call_id 最多发布一次 PENDING（避免与 ApprovalMiddleware 竞态）
    seen_tool_call_ids = set()

    # 策略钩子：循环开始前的事件（深度思考发送"正在深度思考中..."）
    async for event in strategy.on_loop_start(ctx, data):
        yield event

    current_input = graph_input
    while True:
        interrupted = False
        all_resume_values: dict = {}

        logger.debug(
            f"[Loop] astream: input keys="
            f"{list(current_input.keys()) if isinstance(current_input, dict) else type(current_input).__name__}"
        )
        async for chunk in agent.graph.astream(current_input, config=config, stream_mode=["messages", "updates"]):
            # 多 stream mode 下 chunk 是 (mode_name, data) 元组
            if isinstance(chunk, tuple) and len(chunk) == 2:
                mode_name, mode_data = chunk
            else:
                mode_name, mode_data = "messages", chunk

            # ── updates 模式：审批中断检测 ──
            if mode_name == "updates":
                if interrupt_handler is not None:
                    groups = await _collect_chat_approvals(mode_data, ctx, data)
                    if groups:
                        # 产出 approval 事件 + 挂起等待批次决策
                        for group in groups:
                            for event in group["approval_events"]:
                                yield event
                            decisions = await interrupt_handler(
                                group["graph_interrupt_id"],
                                group["langgraph_resume_id"],
                            )
                            if isinstance(decisions, dict):
                                all_resume_values.update(decisions)
                        interrupted = True
                        break
                else:
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
                seen_tool_call_ids=seen_tool_call_ids,
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
                    session_id=data.get("session_id", ""),
                    message_id=str(data.get("_assistant_message_id") or data.get("message_id", "")),
                    module_id=data.get("session_id", ""),
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

        if not interrupted:
            break

        # 构造 Command(resume=...) 重入 astream（key = LangGraph Interrupt.id）
        from langgraph.types import Command

        current_input = Command(resume=all_resume_values)
        logger.info(f"[Loop] 审批恢复重入: resume_keys={list(all_resume_values.keys())}")


async def _collect_chat_approvals(
    mode_data: Any,
    ctx: StreamContext,
    data: dict,
) -> list[dict]:
    """解析 updates chunk 中的审批中断，创建 Approval DB 记录，返回分组数据。

    与 deep_research 的 create_approvals_for_interrupts 同构（仅审批创建方式不同，
    chat 复用 request_approval_async）。返回按 interrupt 分组的列表，供
    _handle_updates_chunk（SSE 模式）与 run_stream_loop 挂起模式共用。

    返回元素结构：
        {
            "graph_interrupt_id": str,
            "langgraph_resume_id": str,
            "approval_events": [{"type": "approval", "data": {...}}, ...],
        }
    """
    if not (isinstance(mode_data, dict) and "__interrupt__" in mode_data):
        return []

    interrupts = mode_data["__interrupt__"]
    if not interrupts:
        return []

    groups: list[dict] = []
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

        approval_events: list[dict] = []
        for approval_data in approval_data_list:
            tool_name = approval_data.get("tool_name", "unknown")
            action = approval_data.get("action", "confirm")
            tool_call_id = approval_data.get("tool_call_id", "")
            logger.info(
                f"approval interrupt: tool={tool_name}, "
                f"action={action}, danger={approval_data.get('danger_level', 'medium')}, "
                f"interrupt_id={approval_data.get('interrupt_id')}, "
                f"graph_interrupt_id={graph_interrupt_id}, "
                f"langgraph_resume_id={langgraph_resume_id}"
            )

            if ctx.session_id:
                approval_data["session_id"] = ctx.session_id
            if ctx.message_id:
                approval_data["message_id"] = ctx.message_id

            # 持久化工具/模型配置到 approval.extra（统一出口，chat/deep_research 共用）
            from Django_xm.apps.approvals.services.approval_service import build_approval_extra
            approval_data["extra"] = build_approval_extra(
                data,
                tool_call_id=tool_call_id,
                graph_interrupt_id=graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
                message_id=ctx.message_id,
                base_extra=approval_data.get("extra"),
            )

            try:
                from Django_xm.apps.approvals.services.approval_service import request_approval_async
                await request_approval_async(
                    source="chat",
                    source_id=ctx.session_id or "",
                    interrupt_id=tool_call_id,
                    approval_data=approval_data,
                )
                logger.info(f"[Approval] DB记录已创建: interrupt_id={tool_call_id}, session={ctx.session_id}")
            except Exception:
                logger.exception(
                    f"[Approval] DB记录创建失败: interrupt_id={tool_call_id}, session={ctx.session_id}"
                )

            # 标记发生了审批中断（用第一个请求的信息）
            if ctx.interrupt_info is None:
                ctx.interrupt_info = {
                    "tool_name": tool_name,
                    "interrupt_id": approval_data.get("interrupt_id"),
                    "graph_interrupt_id": graph_interrupt_id,
                    "langgraph_resume_id": langgraph_resume_id,
                }
            approval_events.append({"type": "approval", "data": approval_data})

        if approval_events:
            groups.append(
                {
                    "graph_interrupt_id": graph_interrupt_id,
                    "langgraph_resume_id": langgraph_resume_id,
                    "approval_events": approval_events,
                }
            )

    return groups


async def _handle_updates_chunk(
    mode_data: Any,
    ctx: StreamContext,
    data: dict,
) -> AsyncGenerator[dict, None]:
    """处理 updates stream mode chunk（审批中断检测，SSE 模式）。

    复用 _collect_chat_approvals 创建审批 + 产出 approval 事件。
    """
    groups = await _collect_chat_approvals(mode_data, ctx, data)
    for group in groups:
        for event in group["approval_events"]:
            yield event
