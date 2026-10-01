"""
审批中断解析器

将 ApprovalMiddleware 产生的 interrupt_value 解析为前端可渲染的 approval_data 列表。

当前唯一支持的输入为**批量格式**（ApprovalMiddleware 默认，middleware.py 为唯一生产者）：
  ``{"_approval": True, "requests": [{tool_name, tool_call_id, ...}], "_meta": {...}}``

历史协议说明：旧版工具级 ``interrupt_for_approval()`` 产出的单工具格式
（``{"_approval": True, "tool_name": ...}``）已随工具内审批机制的移除而删除，
生产仓库中不存在该格式的生产者。本模块对一切非批量格式一律返回 []：
- 带 ``_approval`` 标记但格式非法 → WARNING（疑似 producer 契约漂移，需排查）；
- 非审批 dict → debug（正常防御路径）。
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
        graph_interrupt_id: 兜底批次 id。注意：仅当 ``_meta.graph_interrupt_id``
            缺失时生效；``_meta`` 存在时本参数被忽略（parser L70 以 ``_meta`` 为准）。
            本项目存在两个 id 空间：LangGraph Interrupt.id（``intr.id``，resume
            协议合法 key）与 middleware 批次 uuid（``_meta.graph_interrupt_id``，
            前端分组 + ``Approval.extra`` 查询用），二者同名不同值，勿混淆。
        langgraph_resume_id: resume 协议使用的 id，**必须是 LangGraph Interrupt.id**。
            缺省时条目该字段为空串并记 WARNING（旧"回退批次 uuid"行为已删除——
            批次 uuid 不是合法 resume key，会导致 resume 永不匹配）。调用方
            （chat / research / subagent）必须显式传入 ``intr.id``。

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
        # 日志分级：带 _approval 标记但 requests 非法 = producer/parser 契约漂移信号
        # （下游 assert_non_empty_decision 会把空决策变成 RuntimeError 终止任务，
        # 根因必须默认日志级别可见），升 WARNING；非审批 dict 属正常防御路径
        # （chat 链路上游 is_approval_interrupt 已过滤大部分），保持 DEBUG 防刷屏。
        if interrupt_value.get("_approval"):
            logger.warning(
                "approval interrupt 带 _approval 标记但非批量格式"
                "（requests 类型=%s），返回空（疑似 producer 契约漂移）",
                type(interrupt_value.get("requests")).__name__,
            )
        else:
            logger.debug(
                "approval interrupt 非批量格式（_approval=%r, requests 类型=%s），返回空",
                interrupt_value.get("_approval"),
                type(interrupt_value.get("requests")).__name__,
            )
        return []

    batch_meta = interrupt_value.get("_meta", {})
    resolved_graph_id = batch_meta.get("graph_interrupt_id", graph_interrupt_id)
    # 回退批次 uuid——批次 uuid 不是合法 resume key（LangGraph 按 Interrupt.id
    # 匹配 Command(resume) 键），写进 extra 会静默发起注定失败的恢复。
    # 留空 + WARNING；正常路径三个调用方均显式传入 intr.id，不受影响。
    resolved_langgraph_id = langgraph_resume_id or ""
    if not resolved_langgraph_id:
        logger.warning(
            "approval interrupt 未传 langgraph_resume_id（批次=%s），"
            "条目 resume key 为空（旧行为回退批次 uuid 会导致 resume 永不匹配）",
            resolved_graph_id,
        )
    results = []
    # 重复 interrupt_id 检测（只告警不去重，保持追加行为）：
    # producer 重复 tool_call_id 时，多条请求共享同一 interrupt_id，下游
    # Approval.update_or_create(interrupt_id=...) 会互相覆盖合并成一条，
    # 且 resume dict {tool_call_id: decision} 键冲突——必须可观测。
    seen_interrupt_ids: set[str] = set()
    for req in interrupt_value["requests"]:
        if not isinstance(req, dict):
            continue
        # 共享 interrupt_id，记录互相覆盖且恢复键冲突；改为跳过 + WARNING。
        req_interrupt_id = req.get("tool_call_id") or ""
        if not req_interrupt_id:
            logger.warning(
                "approval interrupt 请求缺失 tool_call_id（tool=%s），跳过该条",
                req.get("tool_name"),
            )
            continue
        if req_interrupt_id in seen_interrupt_ids:
            logger.warning(
                "approval interrupt 同批次出现重复 interrupt_id=%s"
                "（producer 重复），审批记录将互相覆盖/恢复键冲突",
                req_interrupt_id,
            )
        seen_interrupt_ids.add(req_interrupt_id)
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
