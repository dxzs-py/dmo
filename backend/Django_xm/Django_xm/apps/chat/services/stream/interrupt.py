"""finalize_interrupt — 审批中断统一收尾

两种模式（普通 / 深度思考）共用此模块，保证审批中断后行为完全一致：

1. 显式 'interrupted' 事件（修复深度思考模式原缺失的 bug）
2. stream_state 快照写入（content + 统一工具条目容器）

工具调用事件的补发已由单一管线（ToolEventExtractor + publish_tool_lifecycle_event）
在流式过程中实时发布，本模块不再补发工具事件。
"""

import logging
from collections.abc import AsyncGenerator

from .context import StreamContext

logger = logging.getLogger(__name__)


async def finalize_interrupt(
    ctx: StreamContext,
    data: dict,
) -> AsyncGenerator[dict, None]:
    """审批中断统一收尾

    当 ctx.interrupt_info 非空时执行：
    1. 显式 interrupted 事件通知前端
    2. stream_state 快照写入（供 views_chat.py finally 块保存到数据库）

    Args:
        ctx: 流式可变状态
        data: 请求数据
    """
    if ctx.interrupt_info is None:
        return

    # 1. 显式通知前端：本次流因审批中断而结束
    # 修复 bug：深度思考模式原缺失此事件，导致前端无法正确转 INTERRUPTED 状态
    interrupted_payload = {
        "interrupt_id": ctx.interrupt_info.get("interrupt_id"),
        "graph_interrupt_id": ctx.interrupt_info.get("graph_interrupt_id"),
        "tool_name": ctx.interrupt_info.get("tool_name"),
        "reason": "approval_required",
    }
    research_task_id = data.get("research_task_id", "")
    if research_task_id:
        interrupted_payload["research_task_id"] = research_task_id
    yield {
        "type": "interrupted",
        "data": interrupted_payload,
    }

    # 2. stream_state 快照写入
    _write_stream_state_snapshot(ctx, data)


def _write_stream_state_snapshot(ctx: StreamContext, data: dict) -> None:
    """将 current_content 和统一工具条目快照写入 stream_state

    供 views_chat.py finally 块保存到数据库并广播给非请求浏览器，
    确保刷新后工具卡片参数不丢失。

    仅提取可序列化的必要字段，避免引用 ToolMessage 等不可序列化对象。
    工具条目源为统一容器 ``data["_tool_entries"]``（主/子代理共享）。
    """
    if not (ctx.accumulated_reasoning and ctx.accumulated_reasoning.get("_stream_state") is not None):
        return

    ctx.accumulated_reasoning["_stream_state"]["current_content"] = ctx.current_message_content

    tc_snapshot = {}
    for tc_key, tc_val in (data.get("_tool_entries") or {}).items():
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

    ctx.accumulated_reasoning["_stream_state"]["tool_entries"] = tc_snapshot

    logger.info(
        f"[StreamLoop] 审批中断，写入 stream_state 快照: "
        f"tool_calls={len(tc_snapshot)}, content_len={len(ctx.current_message_content)}"
    )
