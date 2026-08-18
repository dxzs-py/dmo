"""统一审批服务层。

审批状态生命周期：
  (none) → pending        request_approval：创建审批，持久化Redis，广播
  pending → processing    resume_approval/timeout_approval：用户审批或超时，设置resume_value，广播
  processing → terminal   complete_approval：approved/rejected/timeout，清理临时字段，广播终态，释放锁

所有状态变更必须经过 _persist_and_broadcast 统一持久化，确保DB、Redis、广播三者一致。
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.approvals.models import Approval, ApprovalOutboxEntry
from Django_xm.apps.approvals.services.approval_constants import (
    APPROVAL_LOCK_PREFIX,
    APPROVAL_LOCK_TTL,
)
from Django_xm.apps.approvals.services.approval_store import (
    get_approval_history as _get_approval_history_from_store,
)
from Django_xm.apps.approvals.services.approval_store import (
    persist_approval_pending,
    persist_approval_processed,
    persist_approval_state,
)
from Django_xm.common.approval_utils import derive_cross_module_id
from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError
from Django_xm.common.observability.approval_metrics import approval_metrics
from Django_xm.common.realtime_sync import (
    _resolve_channels,
    publish_approval_sync,
)
from Django_xm.common.redis_utils import get_redis_client
from Django_xm.common.risk_levels import RiskLevel
from Django_xm.common.tool_call_aggregation import STATE_TO_STATUS
from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_SECONDS = 300

_TEMP_EXTRA_KEYS = ("_resume_value", "_approved", "_timeout")

# remaining_pending_count 未计算哨兵：非 APPROVED 或非批次场景下不注入该字段
_UNSET = object()

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
}


def _now():
    return datetime.now(UTC)


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
    return _resolve_channels(module, module_id, derive_cross_module_id(approval))


def build_approval_payload(
    approval: Approval,
    state: str | None = None,
    scope: str = "broadcast",
    extra: dict | None = None,
    *,
    remaining_pending_count: Any = _UNSET,
) -> dict[str, Any]:
    """审批 payload 单一构造入口（四个 scope 共享字段映射表）。

    收敛原四处构造器（_build_payload / _build_approval_extra_fields /
    _build_approval_sync_fields / _extract_tool_call_event_kwargs），
    新增字段只需在共享映射表登记一次，四个 scope 按需取子集并应用各自规范化，
    避免四处逻辑漂移。

    共享字段映射表（字段名 → 取值函数）：以 approval / approval.extra / state
    为输入、输出原始值；scope 分支负责各自的字段子集与规范化
    （默认值 / 非空过滤 / 路由字段 / timestamp 等）。

    Args:
        approval: Approval 模型实例
        state: 审批状态（persist/broadcast/sync 使用；tool 传 None）
        scope: 输出 scope，persist / broadcast / sync / tool
        extra: 动态附加字段（persist 合并到 payload 顶层；
               broadcast 合并到 extra_fields，可覆盖默认值）
        remaining_pending_count: broadcast scope 的权威剩余 pending 计数。
               默认 _UNSET 不注入；由 sync/async 双路径计算后传入。

    Returns:
        dict: 对应 scope 的 payload
    """
    if scope not in ("persist", "broadcast", "sync", "tool"):
        raise ValueError(f"未知 approval payload scope: {scope!r}")

    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    tool_config = approval_extra.get("tool_config")
    tool_config = tool_config if isinstance(tool_config, dict) else {}

    # ── 共享字段映射表：字段名 → 取值函数（输出原始值，scope 分支再规范化）──
    field_getters: dict[str, Any] = {
        "interrupt_id": lambda: approval.interrupt_id,
        "tool_call_id": lambda: approval_extra.get("tool_call_id") or approval.interrupt_id,
        "tool_name": lambda: approval.tool_name,
        "title": lambda: approval.title,
        "description": lambda: approval.description,
        "operation": lambda: approval.operation,
        "action": lambda: approval.action,
        "user_input": lambda: approval.user_input,
        "parameters": lambda: approval.parameters,
        "source": lambda: approval.source,
        "source_id": lambda: approval.source_id,
        "chat_session_id": lambda: approval.chat_session_id,
        "created_at": lambda: (
            approval.created_at.isoformat().replace("+00:00", "Z") if approval.created_at else None
        ),
        "expires_at": lambda: (
            approval.expires_at.isoformat().replace("+00:00", "Z") if approval.expires_at else None
        ),
        "message_id": lambda: approval_extra.get("message_id"),
        "graph_interrupt_id": lambda: approval_extra.get("graph_interrupt_id"),
        "cross_module_id": lambda: derive_cross_module_id(approval),
        "risk_level": lambda: approval_extra.get("risk_level"),
        # 子 agent 嵌套层级字段（Phase E3，approval_parser.py 已透传至 Approval.extra）
        "parent_tool_call_id": lambda: approval_extra.get("parent_tool_call_id"),
        "depth": lambda: approval_extra.get("depth"),
        "agent_name": lambda: approval_extra.get("agent_name"),
        "agent_path": lambda: approval_extra.get("agent_path"),
        # tool_config 展开（前端用于显示 selected_tools / tool_tier）
        "selected_tools": lambda: tool_config.get("selected_tools"),
        "tool_tier": lambda: tool_config.get("tool_tier"),
        "state": lambda: state or approval.state,
    }

    def _get(field: str) -> Any:
        return field_getters[field]()

    # ── persist：Redis 持久化 payload（原 _build_payload）──
    if scope == "persist":
        session_id, task_id = _resolve_approval_channels(approval)
        # cross_module_id：仅 DEEP_RESEARCH 关联 chat 时有值，前端用于识别跨模块事件
        cross_module_id = _get("cross_module_id")
        payload = {
            "interrupt_id": _get("interrupt_id"),
            "tool_call_id": _get("tool_call_id"),
            "source": _get("source"),
            "source_id": _get("source_id"),
            "session_id": session_id,
            "task_id": task_id,
            "state": _get("state"),
            "tool_name": _get("tool_name"),
            "title": _get("title"),
            "description": _get("description"),
            "action": _get("action"),
            "operation": _get("operation"),
            "parameters": _get("parameters"),
            "user_input": _get("user_input"),
            "extra": approval.extra,
            "created_at": _get("created_at"),
            "expires_at": _get("expires_at"),
            "timestamp": time.time(),
        }
        if cross_module_id:
            payload["cross_module_id"] = cross_module_id
        if extra:
            payload.update(extra)
        # chat_session_id 顶层注入：审批事件的权威会话路由（chat 子代理审批的
        # source_id 可能是 subagent_xxx，但事件必须发到前端订阅的主会话频道，
        # publish_approval 据此覆盖 _resolve_channels 的 session 路由）。
        if approval.chat_session_id:
            payload.setdefault("chat_session_id", approval.chat_session_id)
        # 顶层提取 message_id / graph_interrupt_id / tool_config 展开（setdefault 语义）
        # message_id 可选：chat/deep_research 模块携带用于前端路由，learning 模块无 chat message 可不传
        if approval_extra.get("message_id") is not None:
            payload.setdefault("message_id", approval_extra["message_id"])
        # 批量审批场景下，前端需要 graph_interrupt_id 来收集同一批次的 sibling approvals
        if approval_extra.get("graph_interrupt_id"):
            payload.setdefault("graph_interrupt_id", approval_extra["graph_interrupt_id"])
        if tool_config.get("selected_tools") is not None:
            payload.setdefault("selected_tools", tool_config["selected_tools"])
        if tool_config.get("tool_tier"):
            payload.setdefault("tool_tier", tool_config["tool_tier"])
        return payload

    # ── tool：工具调用事件统一参数（原 _extract_tool_call_event_kwargs）──
    if scope == "tool":
        parameters = _get("parameters")
        payload = {
            "tool_call_id": _get("tool_call_id"),
            "tool_name": _get("tool_name") or "unknown",
            "module": _SOURCE_TO_EVENT_SOURCE.get(approval.source, EventSource.CHAT),
            "module_id": _get("source_id") or "",
            "message_id": approval_extra.get("message_id") or "",
            "parameters": parameters if isinstance(parameters, dict) else {},
            "cross_module_id": _get("cross_module_id"),
            "graph_interrupt_id": approval_extra.get("graph_interrupt_id"),
            "risk_level": approval_extra.get("risk_level") or "",
        }
        # subagent_thread_id 透传：子代理工具事件路由标识（approval.extra 由
        # create_approvals_for_interrupts 写入），前端据其将工具卡归入子代理面板。
        if approval_extra.get("subagent_thread_id"):
            payload["subagent_thread_id"] = approval_extra["subagent_thread_id"]
        return payload

    # ── sync：ChatMessage.tool_calls[].approval 同步字段（原 _build_approval_sync_fields）──
    if scope == "sync":
        sync_fields: dict[str, Any] = {
            "state": _get("state"),
            "interrupt_id": _get("interrupt_id"),
        }
        tc_id = approval_extra.get("tool_call_id") or ""
        if tc_id:
            sync_fields["tool_call_id"] = tc_id
        if approval_extra.get("graph_interrupt_id"):
            sync_fields["graph_interrupt_id"] = approval_extra["graph_interrupt_id"]
        # UI 展示字段（V1/V2 根因修复）
        if approval.title:
            sync_fields["title"] = approval.title
        if approval.description:
            sync_fields["description"] = approval.description
        if approval.operation:
            sync_fields["operation"] = approval.operation
        if approval.parameters:
            sync_fields["parameters"] = approval.parameters
        if approval.tool_name:
            sync_fields["tool_name"] = approval.tool_name
        if approval.action:
            sync_fields["action"] = approval.action
        if approval.user_input:
            sync_fields["user_input"] = approval.user_input
        # 路由字段
        if approval.source:
            sync_fields["source"] = approval.source
        if approval.source_id:
            sync_fields["source_id"] = approval.source_id
        if approval.chat_session_id:
            sync_fields["chat_session_id"] = approval.chat_session_id
        # 时间字段（ISO 格式，与 persist scope 一致）
        created_at = _get("created_at")
        if created_at:
            sync_fields["created_at"] = created_at
        expires_at = _get("expires_at")
        if expires_at:
            sync_fields["expires_at"] = expires_at
        # 透传 extra 中的 message_id（前端依赖此字段精确定位消息）
        if approval_extra.get("message_id") is not None:
            sync_fields["message_id"] = approval_extra["message_id"]
        # 透传 extra 中的 risk_level（统一风险等级字段，优先于 danger_level）
        if approval_extra.get("risk_level"):
            sync_fields["risk_level"] = approval_extra["risk_level"]
        # 补齐缺失字段（Task 2.2）：子 agent 嵌套层级 + tool_config 展开，
        # 与事件 payload / 快照 approval payload 对齐（approval_parser.py 已透传至 extra）
        if approval_extra.get("parent_tool_call_id"):
            sync_fields["parent_tool_call_id"] = approval_extra["parent_tool_call_id"]
        if isinstance(approval_extra.get("depth"), int) and approval_extra["depth"] > 0:
            sync_fields["depth"] = approval_extra["depth"]
        if approval_extra.get("agent_name"):
            sync_fields["agent_name"] = approval_extra["agent_name"]
        if isinstance(approval_extra.get("agent_path"), list) and approval_extra["agent_path"]:
            sync_fields["agent_path"] = approval_extra["agent_path"]
        if tool_config.get("selected_tools") is not None:
            sync_fields["selected_tools"] = tool_config["selected_tools"]
        if tool_config.get("tool_tier"):
            sync_fields["tool_tier"] = tool_config["tool_tier"]
        return sync_fields

    # ── broadcast：publish_approval 调用参数（原 _build_approval_event_params）──
    event_source = _SOURCE_TO_EVENT_SOURCE.get(approval.source, EventSource.CHAT)
    extra_fields: dict[str, Any] = {
        "title": approval.title or "",
        "description": approval.description or "",
        "operation": approval.operation or "",
        "action": approval.action or Approval.ACTION_CONFIRM,
    }
    if approval.user_input:
        extra_fields["user_input"] = approval.user_input
    # 透传 extra 中的关键字段到顶层
    if approval_extra.get("message_id") is not None:
        extra_fields["message_id"] = approval_extra["message_id"]
    if approval_extra.get("graph_interrupt_id"):
        extra_fields["graph_interrupt_id"] = approval_extra["graph_interrupt_id"]
    # risk_level 透传（新标准风险等级，优先于 danger_level，前端 ToolCallCard 显示高危红名）
    if approval_extra.get("risk_level"):
        extra_fields["risk_level"] = approval_extra["risk_level"]
    # seq 透传：从 ToolCallContext 读取（register 唯一分配点），统一经
    # enrich_entry_seq 写入事件 payload。前端审批占位（isSynthetic）据此在 WS
    # 工具事件到达前获得跨浏览器统一排序依据，与 tool_call_* 事件 payload 的
    # seq 保持一致（同一工具调用排序 key 唯一）。
    service.enrich_entry_seq(extra_fields, _get("tool_call_id") or "")
    # position 透传（Agent 图层嵌套规范 D3）：从 ToolCallContext 读取图层内
    # position 写入事件 payload。前端审批占位（isSynthetic）据此在 WS 工具事件
    # 到达前获得图层内联位置，与 tool_call_* 事件 payload 的 position 一致
    # （刷新后审批恢复的工具卡保持内联布局，不排末尾）。
    service.enrich_entry_position(extra_fields, _get("tool_call_id") or "")
    # 子 agent 嵌套层级字段透传（Phase E3，前端展示完整调用链路）
    if approval_extra.get("parent_tool_call_id"):
        extra_fields["parent_tool_call_id"] = approval_extra["parent_tool_call_id"]
    if isinstance(approval_extra.get("depth"), int) and approval_extra["depth"] > 0:
        extra_fields["depth"] = approval_extra["depth"]
    if approval_extra.get("agent_name"):
        extra_fields["agent_name"] = approval_extra["agent_name"]
    if isinstance(approval_extra.get("agent_path"), list) and approval_extra["agent_path"]:
        extra_fields["agent_path"] = approval_extra["agent_path"]
    # tool_config 透传（前端用于显示 selected_tools/tool_tier）
    if tool_config.get("selected_tools") is not None:
        extra_fields["selected_tools"] = tool_config["selected_tools"]
    if tool_config.get("tool_tier"):
        extra_fields["tool_tier"] = tool_config["tool_tier"]
    # 调用方传入的 extra 覆盖默认值（用于 approved_by 等动态字段）
    if extra:
        extra_fields.update(extra)
    # 已决断标记透传：waiting 状态区分"已批准/已拒绝"
    # resume_approval 对批次内已决断审批写入 extra._approved（True=批准 / False=拒绝），
    # 批次其余工具未决断时该审批广播 waiting。前端据此区分"本工具已审批，等待其余"
    # 与"本工具已拒绝，等待其余"，避免拒绝后仍显示"已审批"误导文案。
    if "_approved" in approval_extra:
        extra_fields["approved"] = approval_extra["_approved"]

    parameters = _get("parameters")
    params = {
        "event_type": _APPROVAL_STATE_TO_EVENT_TYPE.get(_get("state"), EventType.APPROVAL_PENDING),
        "interrupt_id": _get("interrupt_id"),
        "tool_call_id": _get("tool_call_id"),
        "module": event_source,
        "module_id": _get("source_id") or "",
        "state": _get("state"),
        "tool_name": _get("tool_name") or "",
        "message_id": approval_extra.get("message_id") or "",
        "parameters": parameters if isinstance(parameters, dict) else {},
        "cross_module_id": _get("cross_module_id"),
        "graph_interrupt_id": approval_extra.get("graph_interrupt_id"),
        "extra_fields": extra_fields,
        # subagent_thread_id：子代理审批事件定向推送路由标识符（spec D10）
        # 子代理审批记录 extra 中携带，主会话审批为空；publish_approval 据此注入事件顶层
        "subagent_thread_id": approval_extra.get("subagent_thread_id") or "",
    }
    # remaining_pending_count 由 sync/async 双路径计算后注入（默认 _UNSET 不注入）
    if remaining_pending_count is not _UNSET:
        params["extra_fields"]["remaining_pending_count"] = remaining_pending_count
    return params


def _build_payload(approval: Approval, state: str | None = None, extra: dict | None = None) -> dict[str, Any]:
    """兼容薄包装：Redis 持久化 payload（原独立构造器，已收敛到 build_approval_payload）。"""
    return build_approval_payload(approval, state, "persist", extra)


def _extract_tool_call_event_kwargs(approval: Approval) -> dict[str, Any]:
    """兼容薄包装：工具调用事件统一参数（原独立构造器，已收敛到 build_approval_payload）。

    所有从 Approval 发布工具调用事件（TIMEOUT/WAITING/RUNNING）的统一参数构造出口，
    避免旧路径（构造 payload + publish_event_sync）导致的双轨发布。
    risk_level 等字段的提取逻辑位于 build_approval_payload（tool scope）共享映射表。
    """
    return build_approval_payload(approval, None, "tool")


def _bind_approval_position(approval: Approval, tool_call_id: str) -> None:
    """将 Approval.extra 中的 position 绑定到 ToolCallContext（keep_existing，幂等）。

    position 由 ApprovalMiddleware 在审批请求中携带（Agent 图层嵌套规范 D3），
    用于流式 extractor 参数不流式（tool_call_chunks args 为空串）时受控工具的
    position 兜底——保证审批 WAITING/RUNNING/TIMEOUT 事件携带图层内联位置，
    前端工具卡不排末尾。
    """
    try:
        _pos = (approval.extra or {}).get("position")
        if isinstance(_pos, int) and _pos >= 0:
            service.bind_position(tool_call_id, _pos)
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 绑定 position 失败(非致命): tool_call_id={tool_call_id}, err={e}"
        )


def _publish_tool_call_timeout_event(approval: Approval):
    """发布 TOOL_CALL_TIMEOUT 事件（通过 tool_call_lifecycle.service.transition 状态机入口）。

    审批超时时，除了发布 APPROVAL_TIMEOUT 更新审批面板，
    还需发布 TOOL_CALL_TIMEOUT 让前端 ToolCallCard 显示"审批超时"状态。

    通过 service.register + service.transition 入口发布：
    - register：幂等注册上下文（chat 模块可能已注册，不覆盖非空字段）
    - transition：状态机校验 + 去重 + 内部调用 publish_tool_call_sync（底层传输不变）
    """
    kwargs = _extract_tool_call_event_kwargs(approval)
    tool_call_id = kwargs["tool_call_id"]
    try:
        # 注册上下文（幂等：已存在时不覆盖非空字段，仅补全空字段）
        # risk_level 透传：让 ToolCallContext 持有风险等级，transition 发布事件时
        # 注入到 tool_call_* 事件 payload，前端从工具事件直接获取风险等级
        service.register(
            ToolCallContext(
                tool_call_id=tool_call_id,
                tool_name=kwargs["tool_name"],
                module=kwargs["module"],
                module_id=kwargs["module_id"],
                message_id=kwargs["message_id"],
                parameters=kwargs["parameters"],
                cross_module_id=kwargs["cross_module_id"],
                graph_interrupt_id=kwargs["graph_interrupt_id"],
                risk_level=kwargs.get("risk_level", ""),
                subagent_thread_id=kwargs.get("subagent_thread_id") or "",
            )
        )
        _bind_approval_position(approval, tool_call_id)
        # 状态机转换并发布事件（内部调用 publish_tool_call_sync）
        service.transition(
            tool_call_id,
            EventType.TOOL_CALL_TIMEOUT,
            parameters=kwargs["parameters"] if isinstance(kwargs["parameters"], dict) else None,
        )
    except PayloadValidationError:
        logger.exception(
            f"[ApprovalService] TOOL_CALL_TIMEOUT payload 校验失败，终止审批流程: "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise  # 审批事件丢失是严重问题，让上层感知
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 发布 TOOL_CALL_TIMEOUT 事件失败: interrupt_id={approval.interrupt_id}, error={e}"
        )


def _publish_tool_call_waiting_event(approval: Approval):
    """发布 TOOL_CALL_WAITING 事件（通过 tool_call_lifecycle.service.transition 状态机入口）。

    语义统一：
    - 审批创建时（state=PENDING）发布：表示工具进入"等待审批"状态
    - 同批次场景（state=WAITING）发布：表示工具已审批但同批次还有其他 pending

    这两种语义都通过同一事件表达，前端根据 approval.state 区分展示。

    通过 service.register + service.transition 入口发布：
    - register：幂等注册上下文（chat 模块可能已注册，不覆盖非空字段）
    - transition：状态机校验 + 去重 + 内部调用 publish_tool_call_sync（底层传输不变）
    """
    kwargs = _extract_tool_call_event_kwargs(approval)
    tool_call_id = kwargs["tool_call_id"]
    try:
        # 注册上下文（幂等：已存在时不覆盖非空字段，仅补全空字段）
        # risk_level 透传：让 ToolCallContext 持有风险等级，transition 发布事件时
        # 注入到 tool_call_* 事件 payload，前端从工具事件直接获取风险等级
        service.register(
            ToolCallContext(
                tool_call_id=tool_call_id,
                tool_name=kwargs["tool_name"],
                module=kwargs["module"],
                module_id=kwargs["module_id"],
                message_id=kwargs["message_id"],
                parameters=kwargs["parameters"],
                cross_module_id=kwargs["cross_module_id"],
                graph_interrupt_id=kwargs["graph_interrupt_id"],
                risk_level=kwargs.get("risk_level", ""),
                subagent_thread_id=kwargs.get("subagent_thread_id") or "",
            )
        )
        _bind_approval_position(approval, tool_call_id)
        # 状态机转换并发布事件（内部调用 publish_tool_call_sync）
        service.transition(
            tool_call_id,
            EventType.TOOL_CALL_WAITING,
            parameters=kwargs["parameters"] if isinstance(kwargs["parameters"], dict) else None,
        )
    except PayloadValidationError:
        logger.exception(
            f"[ApprovalService] TOOL_CALL_WAITING payload 校验失败，终止审批流程: "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise  # 审批事件丢失是严重问题，让上层感知
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 发布 TOOL_CALL_WAITING 事件失败: interrupt_id={approval.interrupt_id}, error={e}"
        )


def _publish_tool_call_running_event(approval: Approval):
    """发布 TOOL_CALL_RUNNING 事件（通过 tool_call_lifecycle.service.transition 状态机入口）。

    审批通过时（state=PROCESSING）发布，表示工具开始执行。
    修复问题 P：审批通过前显示"等待中"，通过后显示"执行中"。

    通过 service.register + service.transition 入口发布：
    - register：幂等注册上下文（chat 模块可能已注册，不覆盖非空字段）
    - transition：状态机校验 + 去重 + 内部调用 publish_tool_call_sync（底层传输不变）
    """
    kwargs = _extract_tool_call_event_kwargs(approval)
    tool_call_id = kwargs["tool_call_id"]
    try:
        # 注册上下文（幂等：已存在时不覆盖非空字段，仅补全空字段）
        # risk_level 透传：让 ToolCallContext 持有风险等级，transition 发布事件时
        # 注入到 tool_call_* 事件 payload，前端从工具事件直接获取风险等级
        service.register(
            ToolCallContext(
                tool_call_id=tool_call_id,
                tool_name=kwargs["tool_name"],
                module=kwargs["module"],
                module_id=kwargs["module_id"],
                message_id=kwargs["message_id"],
                parameters=kwargs["parameters"],
                cross_module_id=kwargs["cross_module_id"],
                graph_interrupt_id=kwargs["graph_interrupt_id"],
                risk_level=kwargs.get("risk_level", ""),
                subagent_thread_id=kwargs.get("subagent_thread_id") or "",
            )
        )
        _bind_approval_position(approval, tool_call_id)
        # 状态机转换并发布事件（内部调用 publish_tool_call_sync）
        service.transition(
            tool_call_id,
            EventType.TOOL_CALL_RUNNING,
            parameters=kwargs["parameters"] if isinstance(kwargs["parameters"], dict) else None,
        )
    except PayloadValidationError:
        logger.exception(
            f"[ApprovalService] TOOL_CALL_RUNNING payload 校验失败，终止审批流程: "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise  # 审批事件丢失是严重问题，让上层感知
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 发布 TOOL_CALL_RUNNING 事件失败: interrupt_id={approval.interrupt_id}, error={e}"
        )


def _count_remaining_pending(graph_interrupt_id: str, exclude_interrupt_id: str) -> int:
    """统计同批次剩余 pending 审批数量（sync ORM 查询）。

    供 sync 路径（_build_approval_event_params）与 async 路径
    （_build_approval_event_params_async，经 sync_to_async 包装复用）共享，
    避免双路径各自内联 ORM 查询导致逻辑漂移。
    """
    return (
        Approval.objects.filter(
            extra__graph_interrupt_id=graph_interrupt_id,
            state=Approval.STATE_PENDING,
        )
        .exclude(interrupt_id=exclude_interrupt_id)
        .count()
    )


_count_remaining_pending_async = sync_to_async(_count_remaining_pending)


def _remaining_pending_condition(approval: Approval, state: str) -> tuple[str, str] | None:
    """判断是否需要计算 remaining_pending_count。

    Returns:
        需要计算时返回 (graph_interrupt_id, interrupt_id)；否则返回 None（不注入字段）。
    """
    if state != Approval.STATE_APPROVED:
        return None
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    graph_interrupt_id = approval_extra.get("graph_interrupt_id")
    if not graph_interrupt_id:
        return None
    return graph_interrupt_id, approval.interrupt_id


def _log_remaining_pending_success(approval: Approval, graph_interrupt_id: str, count: int) -> None:
    logger.info(
        f"[ApprovalService] approval_approved 携带 remaining_pending_count="
        f"{count}, interrupt_id={approval.interrupt_id}, gid={graph_interrupt_id}"
    )


def _log_remaining_pending_failure(approval: Approval, err: Exception) -> int:
    """remaining_pending_count 计算失败：记录 ERROR 并回退 0（保证字段不缺失，不再静默吞掉）。"""
    logger.error(
        f"[ApprovalService] 计算 remaining_pending_count 失败，回退为 0: "
        f"interrupt_id={approval.interrupt_id}, error={err}"
    )
    return 0


def _resolve_remaining_pending_count_sync(approval: Approval, state: str) -> Any:
    """同步路径：计算 remaining_pending_count（仅 APPROVED + 批次场景，否则返回 _UNSET）。"""
    condition = _remaining_pending_condition(approval, state)
    if condition is None:
        return _UNSET
    graph_interrupt_id, interrupt_id = condition
    try:
        count = _count_remaining_pending(graph_interrupt_id, interrupt_id)
        _log_remaining_pending_success(approval, graph_interrupt_id, count)
        return count
    except Exception as e:
        return _log_remaining_pending_failure(approval, e)


async def _resolve_remaining_pending_count_async(approval: Approval, state: str) -> Any:
    """异步路径：await sync_to_async count（修复 async context 直接调 sync ORM 的报错）。"""
    condition = _remaining_pending_condition(approval, state)
    if condition is None:
        return _UNSET
    graph_interrupt_id, interrupt_id = condition
    try:
        count = await _count_remaining_pending_async(graph_interrupt_id, interrupt_id)
        _log_remaining_pending_success(approval, graph_interrupt_id, count)
        return count
    except Exception as e:
        return _log_remaining_pending_failure(approval, e)


def _build_approval_event_params(approval: Approval, state: str, extra: dict | None = None) -> dict:
    """构建 publish_approval_sync 调用参数（同步版，供直接发布与 outbox 补偿复用）。

    统一参数构建出口，确保直接发布与 outbox 补偿使用完全相同的参数集。
    字段逻辑收敛到 build_approval_payload（broadcast scope），本函数仅负责
    remaining_pending_count 的同步计算与注入。
    """
    return build_approval_payload(
        approval,
        state,
        "broadcast",
        extra,
        remaining_pending_count=_resolve_remaining_pending_count_sync(approval, state),
    )


async def _build_approval_event_params_async(approval: Approval, state: str, extra: dict | None = None) -> dict:
    """构建 publish_approval 调用参数（异步版）。

    修复 remaining_pending_count async 报错（根因：sync ORM count 在 async context
    直接调用被 Django 拒绝）：count 查询经 sync_to_async 包装后 await 执行，
    与同步版共用 _remaining_pending_condition / 日志收尾，避免双路径逻辑漂移。
    """
    return build_approval_payload(
        approval,
        state,
        "broadcast",
        extra,
        remaining_pending_count=await _resolve_remaining_pending_count_async(approval, state),
    )


def _create_outbox_entry(approval: Approval, params: dict) -> ApprovalOutboxEntry | None:
    """创建审批事件发件箱条目（Phase C 补偿模式）。

    存储完整的事件参数，供 process_approval_outbox Celery 任务在直接发布失败时重试。
    创建失败不阻塞主流程（仅记日志）。
    """
    event_type = params.get("event_type")
    if event_type is None:
        return None
    try:
        return ApprovalOutboxEntry.objects.create(
            approval=approval,
            event_type=event_type.value if hasattr(event_type, "value") else str(event_type),
            payload=params,
            state=ApprovalOutboxEntry.STATE_PENDING,
        )
    except Exception as e:
        logger.warning(
            f"[ApprovalService] 创建 outbox 条目失败(非致命): interrupt_id={approval.interrupt_id}, error={e}"
        )
        return None


def _mark_outbox_delivered(outbox_entry: ApprovalOutboxEntry | None) -> None:
    """直接发布成功后标记 outbox 条目为已投递。"""
    if outbox_entry is None:
        return
    try:
        outbox_entry.state = ApprovalOutboxEntry.STATE_DELIVERED
        outbox_entry.delivered_at = _now()
        outbox_entry.save(update_fields=["state", "delivered_at"])
    except Exception as e:
        logger.debug(f"[ApprovalService] 标记 outbox delivered 失败(非致命): {e}")


def _broadcast_approval_changed(approval: Approval, state: str, extra: dict | None = None):
    """统一发布审批事件（同步版，通过 publish_approval_sync 统一入口）。

    Phase C 集成：双写 + 补偿模式
    1. 构建事件参数（_build_approval_event_params）
    2. 创建 outbox 条目（补偿用）
    3. 尝试直接发布（publish_approval_sync，实时性）
    4. 直接发布成功 → 标记 outbox 为 delivered
    5. 直接发布失败 → outbox 保持 pending，由 Celery 任务补偿

    PayloadValidationError 仍向上抛出（数据错误不可补偿）。
    """
    params = _build_approval_event_params(approval, state, extra)

    # Phase C: 创建 outbox 条目（补偿用，创建失败不阻塞）
    outbox_entry = _create_outbox_entry(approval, params)

    try:
        publish_approval_sync(**params)
        # 直接发布成功，标记 outbox 为已投递
        _mark_outbox_delivered(outbox_entry)
    except PayloadValidationError:
        # 数据校验错误不可补偿，标记 outbox 为 failed 并向上抛出
        if outbox_entry is not None:
            try:
                outbox_entry.state = ApprovalOutboxEntry.STATE_FAILED
                outbox_entry.error_message = "PayloadValidationError"
                outbox_entry.save(update_fields=["state", "error_message"])
            except Exception:
                # 二次失败（更新 outbox 状态）不掩盖原始 PayloadValidationError
                logger.debug("标记 outbox 为 FAILED 失败", exc_info=True)
        logger.exception(
            f"[ApprovalService] 审批事件 payload 校验失败，终止审批流程: "
            f"state={state}, event_type={params['event_type'].value}, "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise  # 审批事件丢失是严重问题，让上层感知
    except Exception as publish_err:
        # 非 payload 错误（如 Redis 连接失败）：outbox 保持 pending，由补偿任务重试
        logger.exception(
            f"[ApprovalService] 审批事件直接发布失败，已写入 outbox 等待补偿: "
            f"state={state}, event_type={params['event_type'].value}, "
            f"interrupt_id={approval.interrupt_id}"
        )
        # 标记 outbox 的错误信息（保持 pending 状态供补偿）
        if outbox_entry is not None:
            try:
                outbox_entry.error_message = str(publish_err)[:500]
                outbox_entry.save(update_fields=["error_message"])
            except Exception:
                # 二次失败（更新 outbox 错误信息）不掩盖原始发布错误
                logger.debug("更新 outbox 错误信息失败", exc_info=True)


async def _broadcast_approval_changed_async(approval: Approval, state: str, extra: dict | None = None):
    """统一发布审批事件（异步版，通过 publish_approval 异步入口）。

    Phase C 集成：与同步版对称的双写 + 补偿模式。
    参数构建复用 _build_approval_event_params_async（remaining_pending_count 的
    sync ORM count 经 sync_to_async 包装 await 执行，修复 async context 报错），
    确保同步/异步路径参数一致。
    outbox 创建使用 sync_to_async 包装（Django ORM 是同步的）。
    """
    from Django_xm.common.realtime_sync import publish_approval

    params = await _build_approval_event_params_async(approval, state, extra)

    outbox_entry = await sync_to_async(_create_outbox_entry)(approval, params)

    try:
        await publish_approval(**params)
        await sync_to_async(_mark_outbox_delivered)(outbox_entry)
    except PayloadValidationError:
        if outbox_entry is not None:
            try:
                outbox_entry.state = ApprovalOutboxEntry.STATE_FAILED
                outbox_entry.error_message = "PayloadValidationError"
                await sync_to_async(outbox_entry.save)(update_fields=["state", "error_message"])
            except Exception:
                # 二次失败（更新 outbox 状态）不掩盖原始 PayloadValidationError
                logger.debug("异步标记 outbox 为 FAILED 失败", exc_info=True)
        logger.exception(
            f"[ApprovalService] 异步审批事件 payload 校验失败，终止审批流程: "
            f"state={state}, event_type={params['event_type'].value}, "
            f"interrupt_id={approval.interrupt_id}, tool_name={approval.tool_name}",
        )
        raise
    except Exception as publish_err:
        logger.exception(
            f"[ApprovalService] 异步审批事件直接发布失败，已写入 outbox 等待补偿: "
            f"state={state}, event_type={params['event_type'].value}, "
            f"interrupt_id={approval.interrupt_id}"
        )
        if outbox_entry is not None:
            try:
                outbox_entry.error_message = str(publish_err)[:500]
                await sync_to_async(outbox_entry.save)(update_fields=["error_message"])
            except Exception:
                # 二次失败（更新 outbox 错误信息）不掩盖原始发布错误
                logger.debug("异步更新 outbox 错误信息失败", exc_info=True)


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
        return None


def _record_metrics_for_state(approval: Approval, state: str) -> None:
    """根据审批状态变更累加可观测性指标（F2）。

    集成点：_persist_and_broadcast / _persist_and_broadcast_async 末尾统一调用。
    故障隔离：approval_metrics 内部已 try/except，此处不额外捕获。

    状态映射：
        PENDING → on_created（累加 pending + 风险分布）
        APPROVED → on_approved（递减 pending + 记录延迟）
        REJECTED → on_rejected（递减 pending + 记录延迟）
        TIMEOUT → on_timeout（递减 pending + 记录延迟）
        其他状态（PROCESSING/WAITING）→ 不累加（中间态，非终态）
    """
    if state == Approval.STATE_PENDING:
        risk_level = _extract_risk_level(approval)
        approval_metrics.on_created(risk_level)
        # HIGH 级审批创建时记录单会话滑动窗口（供 F3 熔断判定）
        if risk_level == RiskLevel.HIGH and approval.source_id:
            approval_metrics.record_high_risk_for_session(approval.source_id)
    elif state == Approval.STATE_APPROVED:
        approval_metrics.on_approved(_approval_created_timestamp(approval))
    elif state == Approval.STATE_REJECTED:
        approval_metrics.on_rejected(_approval_created_timestamp(approval))
    elif state == Approval.STATE_TIMEOUT:
        approval_metrics.on_timeout(_approval_created_timestamp(approval))


def _persist_and_broadcast(
    approval: Approval,
    state: str,
    extra: dict | None = None,
    suppress_tool_event: bool = False,
):
    """统一状态持久化：DB更新 → Redis同步 → 事件广播 + 工具调用事件联动。

    这是所有审批状态变更的唯一出口，确保三层存储始终一致，并联动发布工具调用事件：
    - state=PENDING：发布 TOOL_CALL_WAITING（工具进入"等待审批"）
    - state=PROCESSING：发布 TOOL_CALL_RUNNING（工具开始执行）
    - state=WAITING：发布 TOOL_CALL_WAITING（同批次还有其他 pending）
    - 终态(approved/rejected/timeout)：写入 Redis processed key，构建终态payload

    Args:
        approval: Approval 模型实例
        state: 目标状态
        extra: 附加数据（透传至事件 payload 与终态 payload）
        suppress_tool_event: 为 True 时跳过工具生命周期事件联动（仅 Redis 同步 +
            审批事件广播）。用于 timeout_approval 的 PROCESSING 中间态——发布
            PROCESSING 后不再发布 RUNNING，避免前端短暂显示"执行中"再变为"超时"。

    修复问题 O/P：审批创建即发布 WAITING（不依赖 batch_size），
                  审批通过即发布 RUNNING（不依赖 stream_helpers 补发）。

    统一底层修复（Z1 次根因）：
    对所有状态（包括 waiting/processing/pending 非终态）调用 sync_approval_state_to_chat_message，
    使 ChatMessage.tool_calls[].approval.state 反映中间态。原实现仅在 complete_approval
    中对终态调用，导致前端 loadSessionDetail 兜底时拿到陈旧 pending 状态而非 waiting。
    修复后：replay 未覆盖的时间窗口内，前端通过 snapshot API 也能拿到正确的中间态。
    """
    is_terminal = state in (Approval.STATE_APPROVED, Approval.STATE_REJECTED, Approval.STATE_TIMEOUT)

    if is_terminal:
        payload = _build_payload(approval, state=state, extra=extra)
        persist_approval_processed(approval.interrupt_id, payload)
    else:
        persist_approval_state(approval.interrupt_id, state)

    # 审批事件广播（统一入口）
    _broadcast_approval_changed(approval, state, extra)

    # 工具调用事件联动（让 ToolCallCard 状态与审批面板分离）
    if not suppress_tool_event:
        if state == Approval.STATE_PENDING:
            # 审批创建：工具进入"等待审批"状态
            _publish_tool_call_waiting_event(approval)
        elif state == Approval.STATE_PROCESSING:
            # 审批通过：工具开始执行。
            # 拒绝路径（extra._approved is False）不发布 RUNNING：resume_approval 对
            # 批次最后一个决断（无论批准/拒绝）统一广播 PROCESSING 以触发 LangGraph 恢复，
            # 但拒绝的工具并未执行，发布 RUNNING 会导致工具状态卡"执行中"，
            # 随后拒绝终态化时 running→rejected 被 tool_call_lifecycle 状态机拦截
            # （非法转换），前端永远收不到 tool_call_rejected 事件。
            extra_dict = approval.extra if isinstance(approval.extra, dict) else {}
            if extra_dict.get("_approved") is not False:
                _publish_tool_call_running_event(approval)
        elif state == Approval.STATE_WAITING:
            # 同批次还有其他 pending：工具保持"等待"状态
            _publish_tool_call_waiting_event(approval)

    # 统一底层修复（Z1 次根因）：对所有状态同步 DB ChatMessage.tool_calls[].approval.state
    # 确保非终态（waiting/processing/pending）也回写 DB，前端 snapshot 兜底能拿到正确中间态。
    # 函数内部有 old_state == state 幂等检查，重复调用无副作用。
    # 用 try/except 保护，DB 同步失败不影响主流程（与 complete_approval 中已有逻辑一致）。
    try:
        sync_approval_state_to_chat_message(approval, state)
    except Exception as sync_err:
        logger.warning(
            f"[ApprovalService] _persist_and_broadcast 同步中间态到 ChatMessage 失败(非致命): "
            f"interrupt_id={approval.interrupt_id}, state={state}, err={sync_err}"
        )

    # 可观测性指标采集（F2）：按终态/创建态累加计数器，故障隔离不影响主流程
    _record_metrics_for_state(approval, state)


def _clean_extra_temp_keys(approval: Approval) -> dict:
    """清理extra中的内部临时字段，返回清理后的extra dict。"""
    extra_data = approval.extra or {}
    if not isinstance(extra_data, dict):
        extra_data = {}
    for key in _TEMP_EXTRA_KEYS:
        extra_data.pop(key, None)
    return extra_data


def _match_tool_call_in_list(tool_calls, approval: Approval):
    """在 tool_calls 列表中查找与 approval 关联的 toolCall。

    匹配策略（与前端 findToolCallById 一致）：
      1. tool_call_id 精确匹配（tc.id 或 tc.tool_call_id 或 tc.approval.tool_call_id）
      2. approval.interrupt_id 匹配（tc.id 或 tc.approval.interrupt_id）
    """
    if not tool_calls:
        return None
    approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
    tool_call_id = approval_extra.get("tool_call_id") or ""
    interrupt_id = approval.interrupt_id or ""

    # 1. tool_call_id 匹配
    if tool_call_id:
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if (
                tc.get("id") == tool_call_id
                or tc.get("tool_call_id") == tool_call_id
                or (isinstance(tc.get("approval"), dict) and tc["approval"].get("tool_call_id") == tool_call_id)
            ):
                return tc

    # 2. interrupt_id 匹配
    if interrupt_id:
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if tc.get("id") == interrupt_id:
                return tc
            appr = tc.get("approval")
            if isinstance(appr, dict) and (
                appr.get("interrupt_id") == interrupt_id or appr.get("tool_call_id") == interrupt_id
            ):
                return tc

    return None


def _apply_sync_fields_to_approval(tc: dict, sync_fields: dict[str, Any]) -> bool:
    """将 sync_fields 应用到 tool_call.approval，返回是否有字段变更。

    仅写入非 None 值，后端为权威源（覆盖本地值）。调用方依赖返回值判断是否需要 save。

    Args:
        tc: tool_call 字典（含 approval 字段）
        sync_fields: build_approval_payload(..., scope='sync') 返回的同步字段字典

    Returns:
        bool: 是否有字段变更
    """
    if not isinstance(tc.get("approval"), dict):
        tc["approval"] = {}
    approval_dict = tc["approval"]

    changed = False
    for key, value in sync_fields.items():
        if value is None:
            continue
        if approval_dict.get(key) != value:
            approval_dict[key] = value
            changed = True
    return changed


def sync_approval_state_to_chat_message(approval: Approval, state: str) -> bool:
    """同步审批完整字段到关联 ChatMessage.tool_calls 的 approval。

    根本性修复（V1/V2）：除 state 外，同步 title/description/operation/danger_level/
    parameters/tool_name/action/user_input 等 UI 字段，确保前端刷新后
    loadSessionDetail 能拿到完整数据，UI 元素不再缺失。

    覆盖全状态链：pending → processing → waiting → approved/rejected/timeout，
    确保刷新后 API 返回的 tool_calls 中 approval 字段反映最新状态与完整 UI 数据。

    深度研究审批通过后，前端非触发浏览器依赖全量同步读取审批状态。
    若后端 Message.tool_calls 中 approval.state 未更新，全量同步会用
    陈旧数据覆盖前端正确的本地状态。

    本函数确保所有审批状态（含 pending 等非终态）被持久化到 Message.tool_calls，
    统一深度研究模式与代理模式的行为（代理模式通过 SSE 流的 _debouncedToolSync
    持续 PATCH）。函数内部有全字段比对幂等检查，重复调用无副作用。

    Args:
        approval: Approval 模型实例
        state: 审批状态（pending/processing/waiting/approved/rejected/timeout）

    Returns:
        bool: 是否成功更新了 Message
    """
    if not approval.chat_session_id:
        return False

    try:
        from django.apps import apps

        ChatMessage = apps.get_model("chat", "ChatMessage")
    except Exception as e:
        logger.warning(f"[ApprovalService] 获取 ChatMessage 模型失败: {e}")
        return False

    # 查找关联的 ChatMessage
    # 优先通过 research_task_id 查找（深度研究场景）
    # 回退到 session 内最新的 assistant 消息
    chat_msg = None
    if approval.source == Approval.SOURCE_DEEP_RESEARCH and approval.source_id:
        chat_msg = (
            ChatMessage.objects.filter(
                research_task_id=approval.source_id,
                role="assistant",
                is_deleted=False,
            )
            .order_by("-created_at")
            .first()
        )

    if chat_msg is None and approval.chat_session_id:
        chat_msg = (
            ChatMessage.objects.filter(
                session__session_id=approval.chat_session_id,
                role="assistant",
                is_deleted=False,
            )
            .order_by("-created_at")
            .first()
        )

    if chat_msg is None:
        logger.info(
            f"[ApprovalService] sync_approval_state_to_chat_message: 未找到关联 ChatMessage, "
            f"interrupt_id={approval.interrupt_id}, chat_session_id={approval.chat_session_id}"
        )
        return False

    tool_calls = list(chat_msg.tool_calls or [])
    target_tc = _match_tool_call_in_list(tool_calls, approval)
    if target_tc is None:
        # 重建缺失的 tool_call 项并追加到 tool_calls
        # 根本性修复：原实现 tool_calls 为空时静默 return False，
        # 导致 approval_pending 时（ChatMessage.tool_calls 尚未保存，时序竞态：
        # request_approval_async 在 views_chat.py L716 调用，而 tool_calls 在
        # finally 块 L826-L881 才保存）approval 字段未写入数据库，
        # 前端刷新后 approval UI 字段（title/description/operation 等）缺失。
        # 删除静默返回，让 tool_calls 为空时也进入重建逻辑，确保 approval 字段及时落库。
        # 同时覆盖 tool_calls 非空但未找到匹配项的场景（tool_call_id 不匹配）。
        # finally 块保存时 merge_existing_approval_fields 会保留此 approval 字段。
        approval_extra = approval.extra if isinstance(approval.extra, dict) else {}
        tool_call_id = approval_extra.get("tool_call_id") or approval.interrupt_id
        target_tc = {
            "id": tool_call_id,
            "tool_call_id": tool_call_id,
            "name": approval.tool_name,
            "args": approval.parameters or {},
            "parameters": approval.parameters or {},
            "input": approval.parameters or {},
            "approval": {},
        }
        # 图层字段补齐（子代理工具卡归集依据）：approval.extra 透传了
        # subagent_thread_id / agent_name / depth / parent_tool_call_id /
        # agent_path（create_approvals_for_interrupts 写入），重建条目据此
        # 还原图层信息，前端 buildSubagentsFromMessage 按 subagent_thread_id
        # 归集子代理工具卡，避免刷新后子代理工具卡脱离子代理卡、按主图层
        # position 切段插入主正文中间（问题 5 修复）。
        for _layer_field in (
            "subagent_thread_id",
            "agent_name",
            "depth",
            "parent_tool_call_id",
            "agent_path",
        ):
            _layer_value = approval_extra.get(_layer_field)
            if _layer_value is not None:
                target_tc[_layer_field] = _layer_value
        # seq 补齐（持久化链路完整性）：重建项统一经 enrich_entry_seq 从
        # ToolCallContext 读取 register 分配的全局递增序号（与 tool_call_* 事件
        # 透传同一来源），确保审批恢复后新追加的 tool_call（如 fs_write_file）
        # 在 API 快照中也带 seq，前端跨浏览器统一排序依据一致（防刷新后工具调用乱序）。
        service.enrich_entry_seq(target_tc, tool_call_id)
        # position 补齐（Agent 图层嵌套规范 D3）：审批恢复后新追加的 tool_call
        # 在 API 快照中也带 position，前端刷新后图层内联布局恢复依据一致
        # （防刷新后工具调用排末尾/乱序）。
        service.enrich_entry_position(target_tc, tool_call_id)
        tool_calls.append(target_tc)
        logger.info(
            f"[ApprovalService] sync_approval_state_to_chat_message: 已重建缺失 tool_call 项, "
            f"msg={chat_msg.id}, interrupt_id={approval.interrupt_id}, "
            f"tool_call_id={tool_call_id}, tool_calls_was_empty={len(tool_calls) == 1}"
        )
        # 继续进入后续的 approval.state 更新逻辑（old_state 为 None，会触发更新）

    # 构造完整同步字段（V1/V2 根因修复：同步全 UI 字段，含嵌套层级与 tool_config 展开）
    sync_fields = build_approval_payload(approval, state, "sync")

    # 全字段比对应用（幂等：无字段变更则跳过写库）
    old_state = target_tc.get("approval", {}).get("state") if isinstance(target_tc.get("approval"), dict) else None
    target_changed = _apply_sync_fields_to_approval(target_tc, sync_fields)

    # B3: 同步 tool_call 顶层 status 字段（与 approval.state 一致），
    # 确保非审批单页（如深度研究快照）也能拿到工具执行状态。
    # 映射值源：common.tool_call_aggregation.STATE_TO_STATUS（唯一权威）。
    mapped_status = STATE_TO_STATUS.get(state, "")
    if mapped_status and target_tc.get("status") != mapped_status:
        target_tc["status"] = mapped_status
        target_changed = True

    if not target_changed and old_state == state:
        # 主字段无变更且 state 未变，跳过写库（保留幂等性）
        return False

    # 同步更新 versions 中的对应 toolCall（全字段）
    versions = chat_msg.versions or []
    version_updated = False
    if isinstance(versions, list) and versions:
        for ver in versions:
            if not isinstance(ver, dict):
                continue
            ver_tcs = ver.get("tool_calls") or []
            if not ver_tcs:
                continue
            ver_tc = _match_tool_call_in_list(ver_tcs, approval)
            if ver_tc is not None:
                if _apply_sync_fields_to_approval(ver_tc, sync_fields):
                    version_updated = True
                # B3: versions 中也同步 tool_call 顶层 status
                if mapped_status and ver_tc.get("status") != mapped_status:
                    ver_tc["status"] = mapped_status
                    version_updated = True

    # 保存
    update_fields = ["tool_calls"]
    if version_updated:
        update_fields.append("versions")
    chat_msg.tool_calls = tool_calls
    if version_updated:
        chat_msg.versions = versions
    chat_msg.save(update_fields=update_fields)

    logger.info(
        f"[ApprovalService] sync_approval_state_to_chat_message: 已同步审批状态(全字段), "
        f"msg={chat_msg.id}, interrupt_id={approval.interrupt_id}, "
        f"old_state={old_state}, new_state={state}, version_updated={version_updated}"
    )
    return True


_sync_approval_state_to_chat_message_async = sync_to_async(sync_approval_state_to_chat_message)


_clean_extra_temp_keys_async = sync_to_async(_clean_extra_temp_keys)


async def _persist_and_broadcast_async(approval: Approval, state: str, extra: dict | None = None):
    """异步版统一状态持久化（与同步版保持一致的联动逻辑）。

    异步路径下也需要联动发布 TOOL_CALL_WAITING / TOOL_CALL_RUNNING 事件，
    避免异步路径下 ToolCallCard 状态不同步。

    统一底层修复（Z1 次根因）：
    与同步版对称，对所有状态（包括非终态）调用 _sync_approval_state_to_chat_message_async，
    使 ChatMessage.tool_calls[].approval.state 反映中间态。
    """
    is_terminal = state in (Approval.STATE_APPROVED, Approval.STATE_REJECTED, Approval.STATE_TIMEOUT)

    if is_terminal:
        payload = _build_payload(approval, state=state, extra=extra)
        persist_approval_processed(approval.interrupt_id, payload)
    else:
        persist_approval_state(approval.interrupt_id, state)

    await _broadcast_approval_changed_async(approval, state, extra)

    # 工具调用事件联动（与同步版一致）
    if state == Approval.STATE_PENDING:
        _publish_tool_call_waiting_event(approval)
    elif state == Approval.STATE_PROCESSING:
        # 与同步版一致：拒绝路径（extra._approved is False）不发布 RUNNING，
        # 避免拒绝工具卡"执行中"且 running→rejected 被状态机拦截。
        extra_dict = approval.extra if isinstance(approval.extra, dict) else {}
        if extra_dict.get("_approved") is not False:
            _publish_tool_call_running_event(approval)
    elif state == Approval.STATE_WAITING:
        _publish_tool_call_waiting_event(approval)

    # 统一底层修复（Z1 次根因）：对所有状态同步 DB ChatMessage.tool_calls[].approval.state
    # 与同步版 _persist_and_broadcast 对称，确保异步路径下中间态也回写 DB。
    try:
        await _sync_approval_state_to_chat_message_async(approval, state)
    except Exception as sync_err:
        logger.warning(
            f"[ApprovalService] _persist_and_broadcast_async 同步中间态到 ChatMessage 失败(非致命): "
            f"interrupt_id={approval.interrupt_id}, state={state}, err={sync_err}"
        )

    # 可观测性指标采集（F2）：与同步版对称，故障隔离不影响主流程
    _record_metrics_for_state(approval, state)


def _acquire_lock(lock_key: str) -> bool:
    """获取审批锁（Redis SET NX）。

    lock_key 为批次维度（graph_interrupt_id）或单审批维度（interrupt_id）。
    批次级锁是批量审批并发竞态的根因修复：同批次工具确认必须串行，
    最后一个获取锁的请求才能看到兄弟全部落库并触发批次恢复。
    """
    redis_client = get_redis_client()
    full_key = f"{APPROVAL_LOCK_PREFIX}{lock_key}"
    return bool(redis_client.set(full_key, "1", nx=True, ex=APPROVAL_LOCK_TTL))


def _acquire_lock_with_retry(lock_key: str, retries: int = 20, delay: float = 0.1) -> bool:
    """带短重试的锁获取：并发确认（同批次多个工具同时提交）时，
    批次锁被他人持有则短暂等待重试，避免确认请求被幂等丢弃导致批次永不完成。"""
    import time as _time

    for _ in range(retries):
        if _acquire_lock(lock_key):
            return True
        _time.sleep(delay)
    return False


def _release_lock(lock_key: str):
    redis_client = get_redis_client()
    full_key = f"{APPROVAL_LOCK_PREFIX}{lock_key}"
    redis_client.delete(full_key)


_release_lock_async = sync_to_async(_release_lock)


# ── 公开 API ──────────────────────────────────────────────

# ApprovalMiddleware 创建的审批请求中需要透传到 Approval.extra 的字段。
# 这些字段不在 Approval 模型字段中，但前端 / 事件 payload 需要它们做展示和审计。
# risk_level: 风险等级（safe/controlled/high，新标准，优先于 danger_level）
# parent_tool_call_id/depth/agent_name/agent_path: 子 agent 嵌套层级（Phase E3）
_EXTRA_PASSTHROUGH_FIELDS = (
    "risk_level",
    "parent_tool_call_id",
    "depth",
    "agent_name",
    "agent_path",
    "graph_interrupt_id",
    "langgraph_resume_id",
    # subagent_thread_id：子代理 SSE 定向推送路由标识符（spec D10）
    "subagent_thread_id",
    # position：middleware 在审批请求中携带的图层内 position（Agent 图层嵌套规范 D3）。
    # 透传写入 Approval.extra，approval_service 发布工具事件时绑定到 ToolCallContext，
    # 审批 WAITING/RUNNING 事件携带 position（受控工具 extractor 参数不流式时兜底）。
    "position",
)


def build_approval_extra(
    data: dict[str, Any],
    *,
    tool_call_id: str = "",
    graph_interrupt_id: str = "",
    langgraph_resume_id: str = "",
    message_id: str = "",
    base_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 Approval.extra 的统一出口（chat / deep_research 共用）。

    所有通过 ``request_approval_async`` 创建审批的模块都应调用此函数，
    确保 extra 中携带完整元数据，供后端 resume 端点（ChatApprovalResume /
    deep_research resume）重建 agent 时读取工具/模型配置，以及前端事件
    payload 读取路由 ID。

    参数说明：
        data: 请求配置 dict（含 tool_config / model_config 等字段）
        tool_call_id: 工具调用 ID（= approval.interrupt_id）
        graph_interrupt_id: 批次 UUID（批量审批分组键）
        langgraph_resume_id: LangGraph Interrupt.id（Command(resume=) KEY）
        message_id: 关联的 chat message ID（前端精确定位消息）
        base_extra: 审批中断解析时已携带的额外字段（如 risk_level / depth 等）

    Returns:
        扁平 dict，作为 Approval.extra 持久化。
    """
    extra: dict[str, Any] = dict(base_extra or {})

    # ── 核心路由 ID ──
    if tool_call_id:
        extra.setdefault("tool_call_id", tool_call_id)
    if graph_interrupt_id:
        extra.setdefault("graph_interrupt_id", graph_interrupt_id)
    if langgraph_resume_id:
        extra.setdefault("langgraph_resume_id", langgraph_resume_id)
    if message_id:
        extra.setdefault("message_id", message_id)

    # ── 工具配置（审批恢复时 ChatApprovalResume 重建 agent 的工具集）──
    extra.setdefault(
        "tool_config",
        {
            "use_tools": data.get("use_tools", True),
            "use_web_search": data.get("use_web_search", False),
            "use_mcp": data.get("use_mcp", False),
            "selected_mcp_servers": data.get("selected_mcp_servers"),
            "selected_tools": data.get("selected_tools"),
            "use_knowledge_base": data.get("use_knowledge_base", False),
            "selected_knowledge_bases": data.get("selected_knowledge_bases", []),
            "tool_tier": data.get("tool_tier", "standard"),
        },
    )

    # ── 模型配置（审批恢复时重建相同模型的 LLM 实例）──
    extra.setdefault(
        "model_config",
        {
            "provider_id": data.get("provider_id"),
            "model_name": data.get("model_name"),
            "use_deep_thinking": data.get("use_deep_thinking", False),
            "special_params": data.get("special_params"),
            "temperature": data.get("temperature"),
            "max_tokens": data.get("max_tokens"),
        },
    )

    return extra


def _merge_passthrough_fields(approval_data: dict[str, Any], extra_data: dict[str, Any]) -> dict[str, Any]:
    """将 risk_level 和子 agent 嵌套字段从 approval_data 透传到 extra_data。

    ApprovalMiddleware 创建的审批请求包含 risk_level 和嵌套层级字段，
    这些字段不在 Approval 模型字段中，需要保存到 extra JSON 字段，
    供 build_approval_payload（broadcast/sync scope）发布到事件 payload 顶层，
    以及 build_approval_index_item 供快照 API 返回。

    已存在 extra_data 中的字段不覆盖（保留先注册的值）。
    """
    for field in _EXTRA_PASSTHROUGH_FIELDS:
        val = approval_data.get(field)
        if val is None or val in ("", []):
            continue
        if not extra_data.get(field):
            extra_data[field] = val
    return extra_data


async def request_approval_async(
    source: str,
    source_id: str,
    interrupt_id: str,
    approval_data: dict[str, Any],
) -> Approval:
    # chat 模块强制要求 chat_session_id（事件路由依赖，M2）
    if source == Approval.SOURCE_CHAT:
        chat_session_id = approval_data.get("session_id")
        if not chat_session_id:
            logger.error(
                f"[ApprovalService] chat 模块审批缺少 chat_session_id，拒绝创建: "
                f"interrupt_id={interrupt_id}, source_id={source_id}"
            )
            raise ValueError(f"chat 模块审批必须显式传入 session_id，interrupt_id={interrupt_id}")
    else:
        chat_session_id = approval_data.get("session_id")
    # 将顶层 message_id 合并到 extra，供 _build_payload 提取到广播 payload 顶层
    # 前端依赖 payload.message_id 精确定位消息（深度研究审批场景）
    extra_data = dict(approval_data.get("extra", {}) or {})
    top_message_id = approval_data.get("message_id")
    if top_message_id and not extra_data.get("message_id"):
        extra_data["message_id"] = top_message_id
    # 透传 risk_level 和子 agent 嵌套字段到 extra（Phase F1 + E3）
    # 注：position 已含于 _EXTRA_PASSTHROUGH_FIELDS，approval_data 顶层携带
    # （approval_batch 已透传 middleware 计算的图层内 position）时自动写入 extra，
    # 供 _bind_approval_position 绑定 ToolCallContext，审批事件携带 position。
    _merge_passthrough_fields(approval_data, extra_data)
    # seq 补齐（持久化链路完整性）：与同步版 request_approval 一致，统一经
    # enrich_entry_seq 从 ToolCallContext 读取 register 分配的全局递增序号写入
    # Approval.extra，使深度研究模块 loadHistory 刷新后重建的 toolCall 带 seq。
    service.enrich_entry_seq(
        extra_data,
        extra_data.get("tool_call_id") or approval_data.get("tool_call_id") or interrupt_id,
    )
    # position 补齐（Agent 图层嵌套规范 D3）：写入 Approval.extra，使深度研究
    # 模块 loadHistory（Approval 为唯一持久化来源）刷新后重建的 toolCall 带
    # position，前端图层内联布局恢复依据一致（防刷新后工具调用排末尾/乱序）。
    service.enrich_entry_position(
        extra_data,
        extra_data.get("tool_call_id") or approval_data.get("tool_call_id") or interrupt_id,
    )

    # M4: 创建时设置 expires_at = now + APPROVAL_TIMEOUT_SECONDS
    from django.utils import timezone as _tz

    _now_ts = _tz.now()
    _expires_at = _now_ts + timedelta(seconds=APPROVAL_TIMEOUT_SECONDS)

    @sync_to_async
    def _create_or_update_approval():
        return Approval.objects.update_or_create(
            interrupt_id=interrupt_id,
            defaults={
                "source": source,
                "source_id": source_id,
                "chat_session_id": chat_session_id,
                "tool_name": approval_data.get("tool_name", ""),
                "title": approval_data.get("title", ""),
                "description": approval_data.get("description", ""),
                "action": approval_data.get("action", Approval.ACTION_CONFIRM),
                "operation": approval_data.get("operation", ""),
                "danger_level": approval_data.get("danger_level", "medium"),
                "parameters": approval_data.get("parameters", {}),
                "state": Approval.STATE_PENDING,
                "user_input": None,
                "extra": extra_data,
                "resolved_at": None,
                "expires_at": _expires_at,
            },
        )

    @sync_to_async
    def _reset_timestamps(approval_obj):
        # M16: 复用 interrupt_id 时同步重置 created_at 和 expires_at
        approval_obj.created_at = _now_ts
        approval_obj.expires_at = _expires_at
        approval_obj.save(update_fields=["created_at", "expires_at"])

    approval, created = await _create_or_update_approval()
    if not created:
        await _reset_timestamps(approval)

    pending_data = _build_payload(approval, state=Approval.STATE_PENDING)
    # persist_approval_pending 写入 approval:pending:{source_id}（待处理列表），
    # 与 _persist_and_broadcast_async 内的 persist_approval_state（approval:processed:{interrupt_id}）不同，
    # 两者职责互补，不可合并。
    persist_approval_pending(source_id, pending_data)

    # 统一发布出口（与 _persist_and_broadcast_async 其他调用方一致，消除双轨发布）：
    # PENDING 广播 + TOOL_CALL_WAITING 联动（问题 O/P）+ ChatMessage.tool_calls
    # 中间态回写（Z1）+ metrics 采集，全部由统一出口完成。
    # 覆盖 M16 复用 interrupt_id 重新发起审批场景。
    await _persist_and_broadcast_async(approval, Approval.STATE_PENDING)

    logger.info(
        f"[ApprovalService] 异步发起审批: source={source}, source_id={source_id}, "
        f"interrupt_id={interrupt_id}, tool={approval.tool_name}"
    )
    return approval


def resume_approval(
    interrupt_id: str,
    approved: bool,
    user_input: str | None = None,
    approved_by: Any | None = None,
) -> dict[str, Any]:
    """将审批从 pending 转为 processing，设置resume_value，广播processing事件。

    幂等处理：
    - 审批不存在：返回 not_found=True（调用方决定如何响应）
    - 审批已是终态(approved/rejected/timeout)：返回 idempotent=True
    - 审批已是processing：返回 idempotent=True（正在处理中）
    - 审批锁被其他线程持有：返回 idempotent=True（并发请求）
    - 深度研究场景 SETNX 锁被持有：返回 idempotent=True, state=processing（并发恢复）

    Args:
        approved_by: 审批操作用户（User 实例或 None），用于 approved_by 字段持久化。
    """
    try:
        approval = Approval.objects.get(interrupt_id=interrupt_id)
    except Approval.DoesNotExist:
        logger.info(f"[ApprovalService] 审批记录不存在: interrupt_id={interrupt_id}")
        return {
            "approval": None,
            "resume_value": None,
            "stream_generator": None,
            "idempotent": True,
            "not_found": True,
        }

    if approval.state in (Approval.STATE_APPROVED, Approval.STATE_REJECTED, Approval.STATE_TIMEOUT):
        logger.info(f"[ApprovalService] 审批已终态，幂等返回: interrupt_id={interrupt_id}, state={approval.state}")
        return {
            "approval": approval,
            "resume_value": None,
            "stream_generator": None,
            "idempotent": True,
        }

    if approval.state == Approval.STATE_PROCESSING:
        logger.info(f"[ApprovalService] 审批处理中(state={approval.state})，幂等返回: interrupt_id={interrupt_id}")
        return {
            "approval": approval,
            "resume_value": None,
            "stream_generator": None,
            "idempotent": True,
            "state": approval.state,
        }

    # 批次级锁（批量审批并发竞态根因修复）：锁粒度从单审批（interrupt_id）提升为
    # 批次（graph_interrupt_id）。原按 interrupt_id 加锁时，同批次多工具并发确认
    # 各持独立锁，事务隔离下彼此读不到对方未提交的 state 更新 → 都判定"还有 pending
    # 兄弟" → 全部置 waiting → 批次永不完成（无最后确认者触发恢复）。
    # 批次锁使同批次确认串行，最后一个获取锁的请求必然看到兄弟全部落库。
    extra_data = approval.extra or {}
    if not isinstance(extra_data, dict):
        extra_data = {}
    graph_interrupt_id = extra_data.get("graph_interrupt_id")
    lock_key = graph_interrupt_id or interrupt_id

    # 锁被持有（并发）时短重试而非直接幂等丢弃，保证并发点击的确认请求都能被处理
    if not _acquire_lock_with_retry(lock_key):
        logger.info(f"[ApprovalService] 审批锁已被持有，幂等返回: lock_key={lock_key}")
        return {
            "approval": approval,
            "resume_value": None,
            "stream_generator": None,
            "idempotent": True,
        }

    try:
        # 获取锁后重读审批（并发下状态可能已变化）
        try:
            approval = Approval.objects.get(interrupt_id=interrupt_id)
        except Approval.DoesNotExist:
            _release_lock(lock_key)
            logger.info(f"[ApprovalService] 锁内重读审批不存在: interrupt_id={interrupt_id}")
            return {
                "approval": None,
                "resume_value": None,
                "stream_generator": None,
                "idempotent": True,
                "not_found": True,
            }
        if approval.state in (Approval.STATE_APPROVED, Approval.STATE_REJECTED, Approval.STATE_TIMEOUT):
            _release_lock(lock_key)
            return {
                "approval": approval,
                "resume_value": None,
                "stream_generator": None,
                "idempotent": True,
            }

        if not approved:
            resume_value = False
        elif approval.action == Approval.ACTION_CONFIRM_WITH_INPUT:
            resume_value = user_input if user_input is not None else ""
        else:
            resume_value = True

        # 批量审批场景：检查同批次是否还有其他 pending
        # - 有：设 state=waiting，广播相应 approval_* 事件，让所有浏览器看到"等待其他工具"提示
        # - 无：设 state=processing，广播 approval_processing 事件，触发 LangGraph 恢复
        extra_data = approval.extra or {}
        if not isinstance(extra_data, dict):
            extra_data = {}
        graph_interrupt_id = extra_data.get("graph_interrupt_id")

        # waiting 状态锁内复查（状态机完整性补全）：并发确认时可能有请求在
        # 兄弟尚未落库时置 waiting，其后无任何请求再检查批次。此处复查：
        # 批次已完整（无 PENDING 兄弟）→ 升级 processing 并触发恢复，杜绝永久卡 waiting。
        if approval.state == Approval.STATE_WAITING:
            has_pending = False
            if graph_interrupt_id:
                has_pending = Approval.objects.filter(
                    extra__graph_interrupt_id=graph_interrupt_id,
                    state=Approval.STATE_PENDING,
                ).exists()
            if has_pending:
                _release_lock(lock_key)
                logger.info(
                    f"[ApprovalService] waiting 复查仍有兄弟 pending，保持等待: "
                    f"interrupt_id={interrupt_id}, graph_interrupt_id={graph_interrupt_id}"
                )
                return {
                    "approval": approval,
                    "resume_value": None,
                    "stream_generator": None,
                    "idempotent": True,
                    "state": Approval.STATE_WAITING,
                }
            approval.state = Approval.STATE_PROCESSING
            approval.save(update_fields=["state"])
            _persist_and_broadcast(approval, Approval.STATE_PROCESSING)
            _release_lock(lock_key)
            logger.info(
                f"[ApprovalService] waiting 复查批次完整，升级 processing 恢复: "
                f"interrupt_id={interrupt_id}, graph_interrupt_id={graph_interrupt_id}"
            )
            return {
                "approval": approval,
                "resume_value": extra_data.get("_resume_value", resume_value),
                "stream_generator": None,
            }

        logger.info(
            f"[ApprovalService] _check_batch: interrupt_id={interrupt_id}, "
            f"graph_interrupt_id={graph_interrupt_id}, "
            f"approval.state={approval.state}, "
            f"approval.source={approval.source}, "
            f"extra_keys={list(extra_data.keys())[:10]}"
        )

        has_pending_siblings = False
        if graph_interrupt_id:
            pending_siblings = Approval.objects.filter(
                extra__graph_interrupt_id=graph_interrupt_id,
                state=Approval.STATE_PENDING,
            ).exclude(interrupt_id=interrupt_id)
            has_pending_siblings = pending_siblings.exists()
            logger.info(
                f"[ApprovalService] sibling_check: interrupt_id={interrupt_id}, "
                f"graph_interrupt_id={graph_interrupt_id}, "
                f"sibling_count={pending_siblings.count()}, "
                f"has_pending={has_pending_siblings}"
            )

        broadcast_state = Approval.STATE_WAITING if has_pending_siblings else Approval.STATE_PROCESSING

        approval.state = broadcast_state
        approval.user_input = user_input
        if approved_by is not None:
            approval.approved_by = approved_by
        extra_data["_resume_value"] = resume_value
        extra_data["_approved"] = approved
        approval.extra = extra_data
        approval.save(update_fields=["state", "user_input", "approved_by", "extra"])

        _persist_and_broadcast(approval, broadcast_state)

        # TOOL_CALL_WAITING / TOOL_CALL_RUNNING 已由 _persist_and_broadcast 统一联动发布：
        # - broadcast_state=WAITING 时发布 TOOL_CALL_WAITING（同批次还有 pending）
        # - broadcast_state=PROCESSING 时发布 TOOL_CALL_RUNNING（工具开始执行）
        # 此处不再重复调用 _publish_tool_call_waiting_event

        logger.info(
            f"[ApprovalService] 恢复审批: interrupt_id={interrupt_id}, "
            f"approved={approved}, source={approval.source}, "
            f"state={broadcast_state}, has_pending_siblings={has_pending_siblings}"
        )

        # waiting 状态：同批次还有其他 pending，不触发恢复流程，直接返回
        # processing 状态：同批次已全部审批完成（或无批次），继续恢复流程
        # 批次级锁在返回前释放，避免阻塞同批次后续确认请求
        if has_pending_siblings:
            _release_lock(lock_key)
            return {
                "approval": approval,
                "resume_value": resume_value,
                "stream_generator": None,
                "state": "waiting",
            }

        _release_lock(lock_key)
        return {
            "approval": approval,
            "resume_value": resume_value,
            "stream_generator": None,
        }
    except Exception:
        _release_lock(lock_key)
        raise


def complete_approval(
    interrupt_id: str,
    state: str,
    extra: dict | None = None,
):
    """将审批从 processing 转为终态(approved/rejected/timeout)。

    清理extra中的内部临时字段，持久化终态到Redis，广播终态事件，释放审批锁。
    若审批记录不存在则静默跳过（幂等）。

    同步清理同批次 graph_interrupt_id 下所有 waiting 状态的 sibling，
    避免 sibling 永久卡在 waiting 状态（Bug 1&6 根因修复）。
    """
    try:
        approval = Approval.objects.get(interrupt_id=interrupt_id)
    except Approval.DoesNotExist:
        logger.warning(f"[ApprovalService] 完成审批失败: 记录不存在, interrupt_id={interrupt_id}")
        return

    approval.state = state
    approval.resolved_at = _now()
    approval.extra = _clean_extra_temp_keys(approval)
    if extra:
        approval.extra.update(extra)
    approval.save(update_fields=["state", "resolved_at", "extra"])

    _persist_and_broadcast(approval, state, extra)
    # 锁 key 与 resume_approval 对称：批次维度（graph_interrupt_id）或单审批维度
    graph_interrupt_id = ""
    if isinstance(approval.extra, dict):
        graph_interrupt_id = approval.extra.get("graph_interrupt_id", "")
    _release_lock(graph_interrupt_id or interrupt_id)

    # 统一底层修复（Z1）：sync_approval_state_to_chat_message 已由 _persist_and_broadcast
    # 统一调用（对所有状态包括终态），此处不再重复调用。原实现的显式调用已合并到
    # _persist_and_broadcast 中，避免双重调用与代码冗余，符合"单一数据源"原则。

    # 委托 ApprovalLifecycleService 统一终态化同批次 siblings（根因 C 修复）
    # 三模块共享同一份代码，删除散落的 _finalize_waiting_siblings_* 实现
    try:
        from Django_xm.common.approval_lifecycle import service as approval_lifecycle_service

        approval_lifecycle_service.complete_batch(
            graph_interrupt_id,
            interrupt_id,
            state,
        )
    except Exception as sib_err:
        logger.warning(
            f"[ApprovalService] 批量终态化失败(非致命): "
            f"interrupt_id={interrupt_id}, graph_interrupt_id={graph_interrupt_id}, "
            f"err={sib_err}"
        )

    logger.info(f"[ApprovalService] 完成审批: interrupt_id={interrupt_id}, state={state}")


def timeout_approval(interrupt_id: str, dispatch_resume: bool = True):
    """审批超时处理：pending→processing(resume_value=TIMEOUT_DECISION)→终态 TIMEOUT。

    超时后设置 resume_value=TIMEOUT_DECISION（而非 False），让 ApprovalMiddleware
    能区分"用户拒绝"和"审批超时"，注入"工具运行失败：审批超时"的 ToolMessage，
    agent 收到后可调整策略继续执行。

    事件驱动化：审批落库终态 TIMEOUT + 发布 approval_timeout 实时事件后，
    发布 Redis 信令唤醒执行服务挂起协程（chat / deep_research 同构）；
    执行器收到信令后重新 collect 校验批次完整性，未全部决断则继续挂起。

    Args:
        interrupt_id: 审批中断 ID（tool_call_id）
        dispatch_resume: 是否触发恢复。自愈/批次判定的内部调用传 False
            （仅终态化，不进入恢复分支），避免递归触发；事件已统一发布。
    """
    from Django_xm.common.constants import TIMEOUT_DECISION

    try:
        approval = Approval.objects.get(interrupt_id=interrupt_id)
    except Approval.DoesNotExist:
        logger.warning(f"[ApprovalService] 超时处理失败: 记录不存在, interrupt_id={interrupt_id}")
        return

    if approval.state != Approval.STATE_PENDING:
        return

    # 锁 key 与 resume_approval 对称：批次维度（graph_interrupt_id）或单审批维度
    extra_data = approval.extra or {}
    if not isinstance(extra_data, dict):
        extra_data = {}
    graph_interrupt_id = extra_data.get("graph_interrupt_id")
    lock_key = graph_interrupt_id or interrupt_id

    if not _acquire_lock(lock_key):
        logger.info(f"[ApprovalService] 超时处理跳过: 锁已被持有, lock_key={lock_key}")
        return

    try:
        approval.state = Approval.STATE_PROCESSING
        extra_data = approval.extra or {}
        if not isinstance(extra_data, dict):
            extra_data = {}
        # 使用 TIMEOUT_DECISION 而非 False，让 middleware 能识别超时并注入超时 ToolMessage
        extra_data["_resume_value"] = TIMEOUT_DECISION
        extra_data["_approved"] = False
        extra_data["_timeout"] = True
        approval.extra = extra_data
        approval.save(update_fields=["state", "extra"])

        # 统一发布出口（与 _persist_and_broadcast 其他调用方一致，消除"绕过统一出口"的
        # PROCESSING 联动语义不一致）：
        # 1. 先持久化 PROCESSING（suppress_tool_event=True：仅 Redis 同步 + APPROVAL_PROCESSING
        #    广播，不联动发布 TOOL_CALL_RUNNING，避免前端短暂显示"执行中"再变为"超时"）
        _persist_and_broadcast(approval, Approval.STATE_PROCESSING, suppress_tool_event=True)

        # 2. 终态：APPROVAL_TIMEOUT（审批面板显示"审批已超时"）。
        #    Redis 终态持久化、ChatMessage.tool_calls 同步（Z1）与 metrics 均由统一出口完成。
        _persist_and_broadcast(approval, Approval.STATE_TIMEOUT, extra={"timeout": True})
        # DB 终态化：_persist_and_broadcast 只写 Redis+广播、不更新 DB，
        # 显式将 DB 状态更新为 TIMEOUT，保证 DB 与 Redis/前端语义一致。
        # 执行器 collect_batch_decisions 以 DB 状态为准，
        # DB 停留在 PROCESSING 会把"超时"误判为"已批准"（resolved=True），
        # 且下一次 cleanup 不再扫描（非 pending），造成终态不可达。
        approval.state = Approval.STATE_TIMEOUT
        approval.save(update_fields=["state"])

        # 工具卡片显示"审批超时"状态（与审批面板分离）
        _publish_tool_call_timeout_event(approval)

        logger.info(
            f"[ApprovalService] 审批超时处理(超时): interrupt_id={interrupt_id}, "
            f"source={approval.source}, source_id={approval.source_id}"
        )

        if not dispatch_resume:
            # 自愈/批次判定内部调用：仅终态化，不触发恢复；执行器轮询 DB 最终一致。
            return

        try:
            from Django_xm.common.approval_gateway import gateway

            gateway.route_timeout(approval, resume_value=TIMEOUT_DECISION)
            logger.info(
                f"[ApprovalService] 超时恢复已路由: source={approval.source}, "
                f"source_id={approval.source_id}, interrupt_id={interrupt_id}"
            )
        except Exception:
            # 信令发布失败不阻断：审批已落库 TIMEOUT 终态，执行器轮询 DB 最终一致
            logger.exception(
                f"[ApprovalService] 超时恢复路由失败: interrupt_id={interrupt_id}"
            )
    except Exception:
        logger.exception("[ApprovalService] 超时处理异常")
        _release_lock(lock_key)


def get_approval_history_by_source(source_id: str) -> list:
    """获取审批历史（pending + processed 合并，直接从Redis读取）。"""
    return _get_approval_history_from_store(source_id)


def get_pending_approvals(source_id: str | None = None, chat_session_id: str | None = None) -> list:
    qs = Approval.objects.filter(state=Approval.STATE_PENDING)
    if source_id:
        qs = qs.filter(source_id=source_id)
    if chat_session_id:
        qs = qs.filter(chat_session_id=chat_session_id)
    return list(qs)


get_approval_history = get_approval_history_by_source
