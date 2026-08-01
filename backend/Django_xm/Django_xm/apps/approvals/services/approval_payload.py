"""审批 payload / 事件参数构建模块（从 approval_service.py 拆分，Task 15.1）。

集中所有纯函数形式的 payload 与事件参数构建逻辑，无副作用、无 I/O：
- 频道路由解析（_resolve_approval_channels）
- 审批事件 payload 构建（_build_payload / _build_tool_call_payload）
- 工具调用事件参数提取（_extract_tool_call_event_kwargs）
- publish_approval_sync 参数构建（_build_approval_event_params / _build_approval_extra_fields）
- 风险等级提取（_extract_risk_level）
- 创建时间戳提取（_approval_created_timestamp）

本模块不直接发布事件，仅构造参数，供 approval_broadcast 与 approval_service 复用。
"""

import logging
import time
from typing import Any

from Django_xm.apps.approvals.models import Approval
from Django_xm.common.event_schema import EventSource, EventType
from Django_xm.common.realtime_sync import _resolve_channels
from Django_xm.common.risk_levels import RiskLevel

logger = logging.getLogger(__name__)

_TEMP_EXTRA_KEYS = ("_resume_value", "_approved", "_timeout")

# Approval.state → EventType 映射
_APPROVAL_STATE_TO_EVENT_TYPE: dict[str, EventType] = {
    Approval.STATE_PENDING: EventType.APPROVAL_PENDING,
    Approval.STATE_PROCESSING: EventType.APPROVAL_PROCESSING,
    Approval.STATE_WAITING: EventType.APPROVAL_WAITING,
    Approval.STATE_APPROVED: EventType.APPROVAL_APPROVED,
    Approval.STATE_REJECTED: EventType.APPROVAL_REJECTED,
    Approval.STATE_TIMEOUT: EventType.APPROVAL_TIMEOUT,
}

# Approval.source 字符串 → EventSource 枚举映射
_SOURCE_TO_EVENT_SOURCE: dict[str, EventSource] = {
    Approval.SOURCE_CHAT: EventSource.CHAT,
    Approval.SOURCE_DEEP_RESEARCH: EventSource.DEEP_RESEARCH,
    Approval.SOURCE_LEARNING: EventSource.LEARNING,
}


def _resolve_approval_channels(approval: Approval) -> tuple[str | None, str | None]:
    """根据 approval 字段解析频道路由（委托 realtime_sync._resolve_channels）。

    三模块统一路由，替代散落的 _get_approval_event_targets：
    - CHAT: session 频道（module_id = source_id = chat_session_id）
    - LEARNING: session 频道（module_id = source_id = thread_id）
    - DEEP_RESEARCH + chat_session_id: 双频道（session:chat_session_id + task:source_id）
    - DEEP_RESEARCH 无 chat_session_id: task 频道（task_id = source_id）

    Returns:
        (session_id, task_id) 二元组，由 _resolve_channels 统一计算
    """
    module = _SOURCE_TO_EVENT_SOURCE.get(approval.source, EventSource.CHAT)
    module_id = approval.source_id or ""
    # cross_module_id 仅 DEEP_RESEARCH 关联 chat 时为 chat_session_id
    cross_module_id = (
        approval.chat_session_id
        if approval.source == Approval.SOURCE_DEEP_RESEARCH and approval.chat_session_id
        else None
    )
    return _resolve_channels(module, module_id, cross_module_id)


def _build_payload(approval: Approval, state: str | None = None, extra: dict | None = None) -> dict[str, Any]:
    # 从 extra 中提取 tool_call_id（ApprovalMiddleware 创建审批时写入）
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    tool_call_id = approval_extra.get("tool_call_id") or approval.interrupt_id

    # 频道路由统一委托 _resolve_approval_channels（三模块共享 _resolve_channels）
    session_id, task_id = _resolve_approval_channels(approval)
    # cross_module_id：仅 DEEP_RESEARCH 关联 chat 时有值，前端用于识别跨模块事件
    cross_module_id = (
        approval.chat_session_id
        if approval.source == Approval.SOURCE_DEEP_RESEARCH and approval.chat_session_id
        else None
    )
    payload = {
        "interrupt_id": approval.interrupt_id,
        "tool_call_id": tool_call_id,
        "source": approval.source,
        "source_id": approval.source_id,
        "session_id": session_id,
        "task_id": task_id,
        "state": state or approval.state,
        "tool_name": approval.tool_name,
        "title": approval.title,
        "description": approval.description,
        "action": approval.action,
        "operation": approval.operation,
        "danger_level": approval.danger_level,
        "parameters": approval.parameters,
        "user_input": approval.user_input,
        "extra": approval.extra,
        "created_at": approval.created_at.isoformat().replace("+00:00", "Z") if approval.created_at else None,
        "expires_at": approval.expires_at.isoformat().replace("+00:00", "Z") if approval.expires_at else None,
        "timestamp": time.time(),
    }
    if cross_module_id:
        payload["cross_module_id"] = cross_module_id
    if extra:
        payload.update(extra)
    # 顶层提取 message_id（重新生成场景下 approval.extra.message_id 携带被重新生成的消息 ID）
    # 便于前端 handleApprovalChanged 直接从 payload.message_id 路由，无需解析 extra 嵌套
    # message_id 可选：chat/deep_research 模块携带用于前端路由，learning 模块无 chat message 可不传
    if isinstance(approval.extra, dict) and approval.extra.get("message_id") is not None:
        payload.setdefault("message_id", approval.extra["message_id"])
    # 顶层提取 graph_interrupt_id，便于前端 sync.js 直接读取批次信息
    # 批量审批场景下，前端需要 graph_interrupt_id 来收集同一批次的 sibling approvals
    if isinstance(approval.extra, dict) and approval.extra.get("graph_interrupt_id"):
        payload.setdefault("graph_interrupt_id", approval.extra["graph_interrupt_id"])
    # 顶层提取 tool_config 中的 selected_tools 和 tool_tier，便于前端直接读取
    if isinstance(approval.extra, dict):
        tool_config = approval.extra.get("tool_config")
        if isinstance(tool_config, dict):
            if tool_config.get("selected_tools") is not None:
                payload.setdefault("selected_tools", tool_config["selected_tools"])
            if tool_config.get("tool_tier"):
                payload.setdefault("tool_tier", tool_config["tool_tier"])
    return payload


def _build_tool_call_payload(approval: Approval) -> dict[str, Any]:
    """从 Approval 构造 ToolCallLifecyclePayload 格式的 payload。

    用于发布 TOOL_CALL_TIMEOUT / TOOL_CALL_WAITING 等工具生命周期事件，
    让前端 ToolCallCard 同步更新工具卡片状态（与审批面板分离）。

    频道路由统一委托 _resolve_approval_channels（三模块共享 _resolve_channels）。
    """
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    tool_call_id = approval_extra.get("tool_call_id") or approval.interrupt_id
    event_source = _SOURCE_TO_EVENT_SOURCE.get(approval.source, EventSource.CHAT)
    session_id, task_id = _resolve_approval_channels(approval)
    cross_module_id = (
        approval.chat_session_id
        if approval.source == Approval.SOURCE_DEEP_RESEARCH and approval.chat_session_id
        else None
    )
    payload = {
        "tool_call_id": tool_call_id,
        "tool_name": approval.tool_name or "unknown",
        "source": event_source,
        "source_id": approval.source_id,
        "session_id": session_id,
        "task_id": task_id,
        "parameters": approval.parameters,
    }
    if cross_module_id:
        payload["cross_module_id"] = cross_module_id
    # 顶层提取 graph_interrupt_id，便于前端 sync.js 直接读取批次信息
    graph_interrupt_id = approval_extra.get("graph_interrupt_id")
    if graph_interrupt_id:
        payload["graph_interrupt_id"] = graph_interrupt_id
    # 顶层提取 message_id（与 _build_payload 一致）
    # message_id 可选：chat/deep_research 模块携带用于前端路由，learning 模块无 chat message 可不传
    if approval_extra.get("message_id") is not None:
        payload["message_id"] = approval_extra["message_id"]
    return payload


def _extract_tool_call_event_kwargs(approval: Approval) -> dict[str, Any]:
    """从 Approval 提取 publish_tool_call_sync 所需的统一参数。

    所有从 Approval 发布工具调用事件（TIMEOUT/WAITING/RUNNING）的统一参数构造出口，
    避免 _build_tool_call_payload + publish_event_sync 的旧路径导致的双轨发布。
    """
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    tool_call_id = approval_extra.get("tool_call_id") or approval.interrupt_id
    event_source = _SOURCE_TO_EVENT_SOURCE.get(approval.source, EventSource.CHAT)
    cross_module_id = (
        approval.chat_session_id
        if approval.source == Approval.SOURCE_DEEP_RESEARCH and approval.chat_session_id
        else None
    )
    return {
        "tool_call_id": tool_call_id,
        "tool_name": approval.tool_name or "unknown",
        "module": event_source,
        "module_id": approval.source_id or "",
        "message_id": approval_extra.get("message_id") or "",
        "parameters": approval.parameters if isinstance(approval.parameters, dict) else {},
        "cross_module_id": cross_module_id,
        "graph_interrupt_id": approval_extra.get("graph_interrupt_id"),
    }


def _build_approval_extra_fields(approval: Approval, extra: dict | None = None) -> dict[str, Any]:
    """构造 publish_approval_sync 的 extra_fields 参数。

    将 Approval 的展示字段（title/description/operation/danger_level/action/user_input）
    以及 extra 中透传的字段（graph_interrupt_id/message_id 等）合并为扁平 dict，
    供 publish_approval_sync 注入 payload 顶层。
    """
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    fields: dict[str, Any] = {
        "title": approval.title or "",
        "description": approval.description or "",
        "operation": approval.operation or "",
        "danger_level": approval.danger_level or "medium",
        "action": approval.action or Approval.ACTION_CONFIRM,
    }
    if approval.user_input:
        fields["user_input"] = approval.user_input
    # 透传 extra 中的关键字段到顶层
    if approval_extra.get("message_id") is not None:
        fields["message_id"] = approval_extra["message_id"]
    if approval_extra.get("graph_interrupt_id"):
        fields["graph_interrupt_id"] = approval_extra["graph_interrupt_id"]
    # risk_level 透传（新标准风险等级，优先于 danger_level，前端 ToolCallCard 显示高危红名）
    if approval_extra.get("risk_level"):
        fields["risk_level"] = approval_extra["risk_level"]
    # 子 agent 嵌套层级字段透传（Phase E3，前端展示完整调用链路）
    if approval_extra.get("parent_tool_call_id"):
        fields["parent_tool_call_id"] = approval_extra["parent_tool_call_id"]
    if isinstance(approval_extra.get("depth"), int) and approval_extra["depth"] > 0:
        fields["depth"] = approval_extra["depth"]
    if approval_extra.get("agent_name"):
        fields["agent_name"] = approval_extra["agent_name"]
    if isinstance(approval_extra.get("agent_path"), list) and approval_extra["agent_path"]:
        fields["agent_path"] = approval_extra["agent_path"]
    # tool_config 透传（前端用于显示 selected_tools/tool_tier）
    tool_config = approval_extra.get("tool_config")
    if isinstance(tool_config, dict):
        if tool_config.get("selected_tools") is not None:
            fields["selected_tools"] = tool_config["selected_tools"]
        if tool_config.get("tool_tier"):
            fields["tool_tier"] = tool_config["tool_tier"]
    # 调用方传入的 extra 覆盖默认值（用于 approved_by 等动态字段）
    if extra:
        fields.update(extra)
    return fields


def _build_approval_event_params(approval: Approval, state: str, extra: dict | None = None) -> dict:
    """构建 publish_approval_sync 调用参数（供直接发布与 outbox 补偿复用）。

    统一参数构建出口，确保直接发布与 outbox 补偿使用完全相同的参数集，
    避免双轨构建导致的事件不一致。
    """
    event_type = _APPROVAL_STATE_TO_EVENT_TYPE.get(state, EventType.APPROVAL_PENDING)
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    tool_call_id = approval_extra.get("tool_call_id") or approval.interrupt_id
    event_source = _SOURCE_TO_EVENT_SOURCE.get(approval.source, EventSource.CHAT)
    cross_module_id = (
        approval.chat_session_id
        if approval.source == Approval.SOURCE_DEEP_RESEARCH and approval.chat_session_id
        else None
    )
    extra_fields_merged = _build_approval_extra_fields(approval, extra)

    # v8: 计算后端权威字段 remaining_pending_count
    if state == Approval.STATE_APPROVED:
        graph_interrupt_id = approval_extra.get("graph_interrupt_id")
        if graph_interrupt_id:
            try:
                remaining_pending_count = (
                    Approval.objects.filter(
                        extra__graph_interrupt_id=graph_interrupt_id,
                        state=Approval.STATE_PENDING,
                    )
                    .exclude(interrupt_id=approval.interrupt_id)
                    .count()
                )
                extra_fields_merged["remaining_pending_count"] = remaining_pending_count
                logger.info(
                    f"[ApprovalService] approval_approved 携带 remaining_pending_count="
                    f"{remaining_pending_count}, interrupt_id={approval.interrupt_id}, "
                    f"gid={graph_interrupt_id}"
                )
            except Exception as e:
                logger.warning(
                    f"[ApprovalService] 计算 remaining_pending_count 失败，忽略: "
                    f"interrupt_id={approval.interrupt_id}, error={e}"
                )

    return {
        "event_type": event_type,
        "interrupt_id": approval.interrupt_id,
        "tool_call_id": tool_call_id,
        "module": event_source,
        "module_id": approval.source_id or "",
        "state": state,
        "tool_name": approval.tool_name or "",
        "message_id": approval_extra.get("message_id") or "",
        "parameters": approval.parameters if isinstance(approval.parameters, dict) else {},
        "cross_module_id": cross_module_id,
        "graph_interrupt_id": approval_extra.get("graph_interrupt_id"),
        "extra_fields": extra_fields_merged,
    }


def _extract_risk_level(approval: Approval) -> RiskLevel:
    """从 Approval.extra 提取风险等级（回退到 danger_level 映射）。"""
    from Django_xm.common.risk_levels import from_danger_level

    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    risk_level_str = approval_extra.get("risk_level")
    if risk_level_str:
        try:
            return RiskLevel(risk_level_str)
        except ValueError:
            pass
    # 回退：从 danger_level 映射
    return from_danger_level(approval.danger_level or "medium")


def _approval_created_timestamp(approval: Approval) -> float | None:
    """从 Approval.created_at 提取 Unix 时间戳（供延迟计算）。"""
    if not approval.created_at:
        return None
    try:
        return approval.created_at.timestamp()
    except Exception:
        logger.debug("提取审批创建时间戳失败", exc_info=True)
        return None
