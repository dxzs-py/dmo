"""
审批中断解析器

将 ApprovalMiddleware 产生的 interrupt_value 解析为前端可渲染的 approval_data 列表。

支持两种格式：
1. **批量格式**（ApprovalMiddleware 默认）：
   ``{"_approval": True, "requests": [{tool_name, tool_call_id, ...}], "_meta": {...}}``
2. **单工具格式**（旧版/工具级 interrupt_for_approval）：
   ``{"_approval": True, "tool_name": "shell_exec", ...}``

所有模块（聊天、深度研究、学习工作流）统一通过此函数解析审批中断值，
消除 adapter.py 中的内联重复代码。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_approval_interrupt(
    interrupt_value: Any,
    graph_interrupt_id: str = "",
    langgraph_resume_id: str = "",
) -> list[dict[str, Any]]:
    """解析审批中断值为 approval_data 列表。

    返回的每个 dict 包含前端需要的标准字段：
    - tool_name, tool_call_id, interrupt_id
    - graph_interrupt_id, langgraph_resume_id
    - title, description, action, danger_level, risk_level
    - parameters, operation, extra, input_placeholder
    - state (固定为 "pending")

    Args:
        interrupt_value: interrupt 的值（dict，需含 _approval 标识）
        graph_interrupt_id: graph 节点的 interrupt id
        langgraph_resume_id: resume 协议使用的 id

    Returns:
        approval_data 列表（批量格式下为多个元素）
    """
    if not isinstance(interrupt_value, dict):
        return []

    # 批量格式：{_approval: True, requests: [...], _meta: {...}}
    if interrupt_value.get("_approval") and isinstance(interrupt_value.get("requests"), list):
        batch_meta = interrupt_value.get("_meta", {})
        resolved_graph_id = batch_meta.get("graph_interrupt_id", graph_interrupt_id)
        resolved_langgraph_id = langgraph_resume_id or resolved_graph_id
        results = []
        for req in interrupt_value["requests"]:
            if not isinstance(req, dict):
                continue
            req_tc_id = req.get("tool_call_id") or req.get("interrupt_id") or ""
            req_interrupt_id = req_tc_id or resolved_graph_id
            entry = _build_approval_entry(
                req=req,
                resolved_graph_id=resolved_graph_id,
                resolved_langgraph_id=resolved_langgraph_id,
                req_interrupt_id=req_interrupt_id,
            )
            results.append(entry)
        return results

    # 单工具格式：{_approval: True, tool_name, ...}
    resolved_langgraph_id = langgraph_resume_id or graph_interrupt_id
    interrupt_id = graph_interrupt_id or interrupt_value.get("interrupt_id", "")
    entry = _build_approval_entry(
        req=interrupt_value,
        resolved_graph_id=graph_interrupt_id,
        resolved_langgraph_id=resolved_langgraph_id,
        req_interrupt_id=interrupt_id,
    )
    return [entry]


def _build_approval_entry(
    req: dict[str, Any],
    resolved_graph_id: str,
    resolved_langgraph_id: str,
    req_interrupt_id: str,
) -> dict[str, Any]:
    """从单个审批请求 dict 构建 approval_data 条目。

    Args:
        req: 审批请求 dict（批量格式中 requests[i] 或单工具格式的 interrupt_value）
        resolved_graph_id: 已解析的 graph_interrupt_id
        resolved_langgraph_id: 已解析的 langgraph_resume_id
        req_interrupt_id: 本请求的 interrupt_id

    Returns:
        approval_data 条目 dict
    """
    entry: dict[str, Any] = {
        "tool_name": req.get("tool_name", "unknown"),
        "tool_call_id": req_interrupt_id,
        "interrupt_id": req_interrupt_id,
        "graph_interrupt_id": resolved_graph_id,
        "langgraph_resume_id": resolved_langgraph_id,
        "title": req.get("title", "确认操作"),
        "description": req.get("description", ""),
        "action": req.get("action", "confirm"),
        "danger_level": req.get("danger_level", "medium"),
        "risk_level": req.get("risk_level"),
        "state": "pending",
        "parameters": req.get("args") or req.get("parameters") or {},
    }

    # 透传 operation（统一字段，兼容旧 command）
    op = req.get("operation") or req.get("command") or ""
    if op:
        entry["operation"] = op

    if req.get("extra"):
        entry["extra"] = req["extra"]
    if req.get("input_placeholder"):
        entry["input_placeholder"] = req["input_placeholder"]

    # 透传子 agent 嵌套层级字段（Phase E3 / P-BE-5 根因修复）：
    # ApprovalMiddleware.aafter_model 在子 agent 执行时为审批请求注入
    # parent_tool_call_id / depth / agent_name / agent_path，
    # 此处透传到 approval_data，供下游（stream_helpers.parse_approval_interrupt
    # → loop._handle_updates_chunk → request_approval_async → Approval.extra）
    # 最终 ChatApprovalResume 通过 approval.extra.depth 识别子 agent interrupt，
    # 跳过主 agent checkpoint namespace 的归属校验。
    if req.get("parent_tool_call_id"):
        entry["parent_tool_call_id"] = req["parent_tool_call_id"]
    if req.get("depth") or req.get("depth") == 0:
        entry["depth"] = req["depth"]
    if req.get("agent_name"):
        entry["agent_name"] = req["agent_name"]
    if req.get("agent_path"):
        entry["agent_path"] = req["agent_path"]
    # subagent_thread_id（spec D10）：子代理 SSE 定向推送路由标识符，
    # 审批事件据此定向到子代理卡片（协议字段，snake_case，不参与 camelCase 转换）
    if req.get("subagent_thread_id"):
        entry["subagent_thread_id"] = req["subagent_thread_id"]

    return entry
