"""run_stream_loop — 核心流式循环（单一底层模块）

chat_service 共用的唯一 astream 循环实现。
保证以下行为在普通模式和深度思考模式下完全一致：

- astream stream_mode=["messages", "updates"]
- updates 模式：审批中断检测（extract_interrupt_ids + parse_approval_interrupt）
- messages 模式：process_stream_chunk 调用 + 事件分发
- chunk 累积到 ctx.current_message_content
- asyncio.sleep(0.01) 让出事件循环

不包含：韧性重试、超时（由 ResilienceRunner 包装）
不包含：审批中断收尾、finalize、建议生成（由 interrupt.py / finalizer.py 处理）
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict

from Django_xm.apps.chat.services.stream_helpers import (
    extract_interrupt_ids,
    parse_approval_interrupt,
    process_stream_chunk,
)
from Django_xm.apps.chat.utils import _lcp_len
from Django_xm.apps.tools.base import is_approval_interrupt

from .context import StreamContext
from .strategy import BaseStreamStrategy

logger = logging.getLogger(__name__)


async def run_stream_loop(
    agent,
    graph_input: Dict,
    config: Dict,
    ctx: StreamContext,
    strategy: BaseStreamStrategy,
    data: Dict,
) -> AsyncGenerator[Dict, None]:
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

    async for chunk in agent.graph.astream(
        graph_input, config=config, stream_mode=["messages", "updates"]
    ):
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
        ctx.all_messages.append(
            mode_data if not isinstance(mode_data, tuple) else mode_data[0]
        )

        try:
            for event in process_stream_chunk(
                mode_data, ctx.tool_calls_map, ctx.current_message_content,
                tool_call_count=ctx.tool_call_count,
                lcp_func=_lcp_len,
                accumulated_reasoning=ctx.accumulated_reasoning,
                tool_args_accumulator=ctx.tool_args_accumulator,
                mode=data.get('mode', 'agent'),
                enable_deep_thinking=strategy.enable_deep_thinking,
                session_id=data.get('session_id'),
                message_id=data.get('_assistant_message_id'),
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
    data: Dict,
) -> AsyncGenerator[Dict, None]:
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
            tool_name = approval_data.get('tool_name', 'unknown')
            action = approval_data.get('action', 'confirm')
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
                    "interrupt_id": approval_data.get('interrupt_id'),
                    "graph_interrupt_id": graph_interrupt_id,
                    "langgraph_resume_id": langgraph_resume_id,
                }
            yield {
                'type': 'approval',
                'data': approval_data,
            }
