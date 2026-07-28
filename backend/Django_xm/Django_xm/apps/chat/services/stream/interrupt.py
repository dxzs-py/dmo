"""finalize_interrupt — 审批中断统一收尾

两种模式（普通 / 深度思考）共用此模块，保证审批中断后行为完全一致：

1. finalize_tool_calls 补发工具事件
2. 显式 'interrupted' 事件（修复深度思考模式原缺失的 bug）
3. stream_state 快照写入（content + tool_calls_map）

修复的 bug：
- 深度思考模式原不发送 'interrupted' 事件，导致前端无法正确转 INTERRUPTED 状态
- 两套 stream_state 写入逻辑现已统一为单一实现
"""

import logging
from collections.abc import AsyncGenerator

from Django_xm.apps.chat.services.stream_helpers import finalize_tool_calls

from .context import StreamContext

logger = logging.getLogger(__name__)


async def finalize_interrupt(
    ctx: StreamContext,
    data: dict,
) -> AsyncGenerator[dict, None]:
    """审批中断统一收尾

    当 ctx.interrupt_info 非空时执行：
    1. finalize_tool_calls 补发工具 SSE 事件
    2. 显式 interrupted 事件通知前端
    3. stream_state 快照写入（供 views_chat.py finally 块保存到数据库）

    Args:
        ctx: 流式可变状态
        data: 请求数据
    """
    if ctx.interrupt_info is None:
        return

    logger.info(
        f"审批中断，执行 finalize_tool_calls 补发工具事件: "
        f"tool={ctx.interrupt_info.get('tool_name')}, "
        f"graph_interrupt_id={ctx.interrupt_info.get('graph_interrupt_id')}"
    )

    # 1. finalize_tool_calls 补发工具事件
    for tool_update_event in finalize_tool_calls(
        ctx.all_messages, ctx.tool_calls_map, ctx.tool_args_accumulator,
        session_id=data.get('session_id'),
        message_id=data.get('_assistant_message_id'),
    ):
        yield tool_update_event

    # 2. 显式通知前端：本次流因审批中断而结束
    # 修复 bug：深度思考模式原缺失此事件，导致前端无法正确转 INTERRUPTED 状态
    yield {
        'type': 'interrupted',
        'data': {
            'interrupt_id': ctx.interrupt_info.get('interrupt_id'),
            'graph_interrupt_id': ctx.interrupt_info.get('graph_interrupt_id'),
            'tool_name': ctx.interrupt_info.get('tool_name'),
            'reason': 'approval_required',
        },
    }

    # 3. stream_state 快照写入
    _write_stream_state_snapshot(ctx)


def _write_stream_state_snapshot(ctx: StreamContext) -> None:
    """将 current_content 和 tool_calls_map 快照写入 stream_state

    供 views_chat.py finally 块保存到数据库并广播给非请求浏览器，
    确保刷新后工具卡片参数不丢失。

    仅提取可序列化的必要字段，避免引用 ToolMessage 等不可序列化对象。
    """
    if not (ctx.accumulated_reasoning
            and ctx.accumulated_reasoning.get("_stream_state") is not None):
        return

    ctx.accumulated_reasoning["_stream_state"]["current_content"] = ctx.current_message_content

    tc_snapshot = {}
    for tc_key, tc_val in (ctx.tool_calls_map or {}).items():
        if not isinstance(tc_val, dict):
            continue
        tc_snapshot[tc_key] = {
            "id": tc_val.get("id", ""),
            "name": tc_val.get("name", ""),
            "type": tc_val.get("type", ""),
            "state": tc_val.get("state", ""),
            "status": tc_val.get("status", ""),
            "parameters": tc_val.get("parameters", {}) or {},
        }
        if tc_val.get("result") is not None:
            tc_snapshot[tc_key]["result"] = tc_val.get("result")
        if tc_val.get("error") is not None:
            tc_snapshot[tc_key]["error"] = tc_val.get("error")

    ctx.accumulated_reasoning["_stream_state"]["tool_calls_map"] = tc_snapshot

    logger.info(
        f"[StreamLoop] 审批中断，写入 stream_state 快照: "
        f"tool_calls={len(tc_snapshot)}, content_len={len(ctx.current_message_content)}"
    )
