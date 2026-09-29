"""
审批中断解析器

将 ApprovalMiddleware 产生的 interrupt_value 解析为前端可渲染的 approval_data 列表。

当前唯一支持的输入为**批量格式**（ApprovalMiddleware 默认，middleware.py 为唯一生产者）：
  ``{"_approval": True, "requests": [{tool_name, tool_call_id, ...}], "_meta": {...}}``

历史协议说明：旧版工具级 ``interrupt_for_approval()`` 产出的单工具格式
（``{"_approval": True, "tool_name": ...}``）已随工具内审批机制的移除而删除，
生产仓库中不存在该格式的生产者。本模块对一切非批量格式一律返回 [] 并记 debug，
不再静默兼容（避免任意 dict 被误解析为审批条目）。

所有模块（聊天、深度研究、学习工作流、子代理运行时）统一通过此函数解析审批中断值。
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
    - parameters, operation
    - 子代理嵌套字段（存在时透传）：parent_tool_call_id, depth,
      agent_name, agent_path, subagent_thread_id

    注意：不输出 state 字段——审批状态的初始化由前端管理
    （approvalStore._handleNewApproval 强制 state='pending'），
    持久化状态由 Approval 模型（request_approval_async）管理。

    Args:
        interrupt_value: interrupt 的值（dict，需含 _approval 标识与 requests 列表）
        graph_interrupt_id: graph 节点的 interrupt id（_meta 缺省时回退）
        langgraph_resume_id: resume 协议使用的 id

    Returns:
        approval_data 列表（批量格式下为多个元素；非法输入返回 []）
    """
    if not isinstance(interrupt_value, dict):
        logger.debug(
            "approval interrupt 非法输入（非 dict: %s），返回空",
            type(interrupt_value).__name__,
        )
        return []

    # 批量格式：{_approval: True, requests: [...], _meta: {graph_interrupt_id}}
    if not (interrupt_value.get("_approval") and isinstance(interrupt_value.get("requests"), list)):
        # 不兼容单工具格式（历史协议，生产者已删除）；任意非批量 dict 一律拒绝。
        logger.debug(
            "approval interrupt 非批量格式（_approval=%r, requests 类型=%s），返回空",
            interrupt_value.get("_approval"),
            type(interrupt_value.get("requests")).__name__,
        )
        return []

    batch_meta = interrupt_value.get("_meta", {})
    resolved_graph_id = batch_meta.get("graph_interrupt_id", graph_interrupt_id)
    resolved_langgraph_id = langgraph_resume_id or resolved_graph_id
    results = []
    for req in interrupt_value["requests"]:
        if not isinstance(req, dict):
            continue
        req_interrupt_id = req.get("tool_call_id") or resolved_graph_id
        results.append(
            _build_approval_entry(
                req=req,
                resolved_graph_id=resolved_graph_id,
                resolved_langgraph_id=resolved_langgraph_id,
                req_interrupt_id=req_interrupt_id,
            )
        )
    return results


def _build_approval_entry(
    req: dict[str, Any],
    resolved_graph_id: str,
    resolved_langgraph_id: str,
    req_interrupt_id: str,
) -> dict[str, Any]:
    """从单个审批请求 dict 构建 approval_data 条目。

    Args:
        req: 审批请求 dict（批量格式中 requests[i]）
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
        # 不设默认值：None 供下游（approval_batch）区分"未评估"
        "risk_level": req.get("risk_level"),
        # 生产者（middleware L805）恒产 args；parameters 为对外契约字段名
        "parameters": req.get("args") or {},
    }

    if req.get("operation"):
        entry["operation"] = req["operation"]

    # 子代理嵌套层级字段（Phase E3 / P-BE-5 根因修复）：
    # ApprovalMiddleware 在子 agent 执行时为审批请求注入，此处透传到 approval_data，
    # 下游 request_approval_async._merge_passthrough_fields 写入 Approval.extra，
    # ChatApprovalResume 据此识别子 agent interrupt、跳过主 agent checkpoint 归属校验。
    if req.get("parent_tool_call_id"):
        entry["parent_tool_call_id"] = req["parent_tool_call_id"]
    if req.get("depth") is not None:
        entry["depth"] = req["depth"]
    if req.get("agent_name"):
        entry["agent_name"] = req["agent_name"]
    if req.get("agent_path"):
        entry["agent_path"] = req["agent_path"]
    # subagent_thread_id（spec D10）：子代理 SSE 定向推送路由标识符
    # （协议字段，snake_case，不参与 camelCase 转换）
    if req.get("subagent_thread_id"):
        entry["subagent_thread_id"] = req["subagent_thread_id"]

    return entry
