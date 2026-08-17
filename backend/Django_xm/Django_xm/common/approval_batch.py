"""审批批次决策/终态化/创建公共模块（chat / research 双链路唯一实现）。

将原 chat（``chat_executor_core._collect_chat_batch_decisions`` /
``_finalize_chat_batch_approvals``）与 research（``research_runner.collect_batch_decisions`` /
``finalize_batch_approvals`` / ``create_approvals_for_interrupts``）仅 source 不同的
重复逻辑收敛为本模块，通过 ``source`` 参数区分，避免"修一处漏一处"。
"""

from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.approvals.models import Approval
from Django_xm.common.constants import TIMEOUT_DECISION


def _find_approval_by_interrupt_id(interrupt_id: str):
    """按 interrupt_id 查询审批记录（审批创建幂等保护用）。"""
    return Approval.objects.filter(interrupt_id=interrupt_id).first()


def collect_batch_decisions(
    source: str,
    source_id: str,
    graph_interrupt_id: str,
) -> tuple[dict, bool]:
    """收集同批次所有审批决策。

    Command(resume=...) 的 key 必须是 LangGraph Interrupt.id（langgraph_resume_id）。

    Args:
        source: 审批来源（``Approval.SOURCE_CHAT`` / ``Approval.SOURCE_DEEP_RESEARCH``）。
        source_id: 会话/任务 ID（chat=session_id, deep_research=task_id）。
        graph_interrupt_id: 批次 ID。

    Returns:
        (resume_by_interrupt, all_resolved)
        - resume_by_interrupt: {langgraph_resume_id: {tool_call_id: bool|TIMEOUT_DECISION}}
        - all_resolved: 是否所有审批都已决断（approved/rejected/timeout）
    """
    batch_approvals = Approval.objects.filter(
        source=source,
        source_id=source_id,
        extra__graph_interrupt_id=graph_interrupt_id,
    )

    resume_by_interrupt: dict[str, dict[str, Any]] = {}
    all_resolved = True

    for approval in batch_approvals:
        extra = approval.extra if isinstance(approval.extra, dict) else {}
        tc_id = extra.get("tool_call_id", approval.interrupt_id)
        langgraph_id = extra.get("langgraph_resume_id", graph_interrupt_id)

        if approval.state == Approval.STATE_APPROVED:
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = True
        elif approval.state in (Approval.STATE_PROCESSING, Approval.STATE_WAITING):
            # PROCESSING/WAITING 实际决策写入 extra._approved，
            # 缺失 _approved 时默认视为确认（True）
            _approved = extra.get("_approved")
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = bool(
                _approved is None or _approved is True
            )
        elif approval.state == Approval.STATE_REJECTED:
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = False
        elif approval.state == Approval.STATE_TIMEOUT:
            # 超时决策标记（TIMEOUT_DECISION）：与拒绝（False）区分，
            # middleware 据以注入"审批超时"ToolMessage，agent 调整策略继续
            resume_by_interrupt.setdefault(langgraph_id, {})[tc_id] = TIMEOUT_DECISION
        else:
            all_resolved = False

    return resume_by_interrupt, all_resolved


def finalize_batch_approvals(all_resume_values: dict, source: str, source_id: str) -> None:
    """审批终态化：对批次内所有已决断的审批记录调用 complete_approval。

    Args:
        all_resume_values: {langgraph_resume_id: {tool_call_id: bool}} 或 {tool_call_id: bool}。
        source: 审批来源（``Approval.SOURCE_CHAT`` / ``Approval.SOURCE_DEEP_RESEARCH``）。
        source_id: 会话/任务 ID。
    """
    from django.db import models

    from Django_xm.apps.approvals.services.approval_service import complete_approval

    flat_decisions: dict[str, bool] = {}
    for key, val in all_resume_values.items():
        if isinstance(val, dict):
            flat_decisions.update(val)
        elif isinstance(val, bool):
            flat_decisions[key] = val

    for tc_or_int_id, decision in flat_decisions.items():
        try:
            approval = Approval.objects.filter(
                source=source,
                source_id=source_id,
            ).filter(
                models.Q(interrupt_id=tc_or_int_id) | models.Q(extra__tool_call_id=tc_or_int_id)
            ).first()

            if approval is None:
                continue

            if approval.state in (Approval.STATE_PROCESSING, Approval.STATE_WAITING):
                final_state = Approval.STATE_APPROVED if decision is True else Approval.STATE_REJECTED
                complete_approval(approval.interrupt_id, final_state)
        except Exception:
            # 终态化失败不阻断恢复流程（幂等，下次重试可补齐）
            continue


async def create_approvals_for_interrupts(
    interrupts_data,
    *,
    source: str,
    source_id: str,
    user_id: int | None = None,
    chat_session_id: str | None = None,
    message_id: str = "",
    data: dict[str, Any] | None = None,
) -> str:
    """为已解析的审批中断批量创建 Approval DB 记录。

    chat 与 research 双链路共用本函数（通过 ``source`` 区分）：
    - 共用 ``request_approval_async``（统一持久化）与 ``build_approval_extra``；
    - 幂等保护：同一 interrupt_id 已有已决断审批（非 PENDING）时不重置 state，
      避免恢复执行中 agent 再次经过已决断 interrupt 暂停点时用户决策被覆盖。

    Args:
        interrupts_data: 单个 interrupt dict 或 interrupt dict list（已解析的
            approval_data 列表，来自 ``common.approval_parser.parse_approval_interrupt``）。
        source: 审批来源（``Approval.SOURCE_CHAT`` / ``Approval.SOURCE_DEEP_RESEARCH``）。
        source_id: 审批归属 ID（chat=session_id, deep_research=task_id）。
        user_id: 任务归属用户 ID（approval.user 外键）。
        chat_session_id: 关联 chat 会话 ID（跨模块同步事件路由）。
        message_id: 关联 chat message ID（前端定位）。
        data: 任务上下文数据（透传到 approval extra）。

    Returns:
        str: 批次 ID（graph_interrupt_id），无有效审批时返回空字符串。
    """
    if isinstance(interrupts_data, dict):
        interrupts_data = [interrupts_data]
    if not interrupts_data:
        return ""

    from Django_xm.apps.approvals.services.approval_service import (
        build_approval_extra,
        request_approval_async,
    )

    first_interrupt = interrupts_data[0] if interrupts_data else {}
    graph_interrupt_id = first_interrupt.get("graph_interrupt_id", "") or ""
    langgraph_resume_id = first_interrupt.get("langgraph_resume_id", "") or ""

    for interrupt_data in interrupts_data:
        interrupt_id = interrupt_data.get("interrupt_id", "")
        if not interrupt_id:
            continue

        tool_name = interrupt_data.get("tool_name", "unknown")
        tool_call_id = interrupt_data.get("tool_call_id", "") or interrupt_id

        base_extra: dict[str, Any] = {}
        risk_level = interrupt_data.get("risk_level")
        if risk_level:
            base_extra["risk_level"] = risk_level
        for field in ("parent_tool_call_id", "depth", "agent_name", "agent_path", "subagent_thread_id"):
            val = interrupt_data.get(field)
            if val is not None and val not in ("", []):
                base_extra[field] = val

        approval_data = {
            "tool_name": tool_name,
            "title": interrupt_data.get("title", "确认操作"),
            "description": interrupt_data.get("description", ""),
            "operation": interrupt_data.get("operation", ""),
            "danger_level": interrupt_data.get("danger_level", "medium"),
            "parameters": interrupt_data.get("parameters", {}) or interrupt_data.get("args", {}) or {},
            "action": interrupt_data.get("action", Approval.ACTION_CONFIRM),
            "session_id": chat_session_id,
            "message_id": message_id,
            "extra": build_approval_extra(
                data or {},
                tool_call_id=tool_call_id,
                graph_interrupt_id=graph_interrupt_id,
                langgraph_resume_id=langgraph_resume_id,
                message_id=message_id,
                base_extra=base_extra,
            ),
        }

        # 幂等保护：同一 interrupt_id 已有已决断审批（非 PENDING）时跳过
        _existing = await sync_to_async(_find_approval_by_interrupt_id)(interrupt_id)
        if _existing is not None and _existing.state != Approval.STATE_PENDING:
            continue

        try:
            await request_approval_async(
                source=source,
                source_id=source_id,
                interrupt_id=interrupt_id,
                approval_data=approval_data,
            )
        except Exception:
            # 创建失败不阻断其它审批，由上层决策链兜底
            continue

    return graph_interrupt_id


def assert_non_empty_decision(result) -> None:
    """审批决策不得为空：空 dict 表示审批创建失败，直接抛错终止任务。

    会话级单执行流语义：interrupt 回调必须返回非空决策（审批批次已创建），
    空返回意味着审批链路异常，任务无法等待恢复，只能终止。
    chat 与 research 双链路共用（抽象为模块级函数，避免 astream 循环
    try 块内直接 raise 的 TRY301）。
    """
    if isinstance(result, dict) and not result:
        raise RuntimeError(
            "审批创建失败：审批中断回调返回空决策，无审批批次可等待（任务终止）"
        )
