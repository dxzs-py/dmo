"""
统一实时事件 Schema 定义

所有实时事件 payload 必须符合本模块定义的 TypedDict，不允许 **extra 开放透传。

事件类型分层：
- 工具调用生命周期事件（7 个）：覆盖 pending → waiting/running →
  completed/failed/timeout/rejected 的完整状态机
- 审批事件（5 个）：覆盖 pending → processing → approved/rejected/timeout
- 流式事件（5 个）：reasoning/sources/suggestions/context/content_update
- 会话/消息事件（5 个）：session_created/updated/deleted + message_updated/deleted

状态由 event_type 本身表达，payload 不再携带 state 字段（审批事件除外，
审批 state 字段保留用于前端 ApprovalStore 的状态映射，与 event_type 1:1 对应）。

使用方式：
    from Django_xm.common.event_schema import EventType, EventSource, validate_payload

    validate_payload(EventType.TOOL_CALL_RUNNING, payload)

校验失败时抛出 PayloadValidationError，调用方应捕获并记录日志。
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class PayloadValidationError(Exception):
    """payload 校验失败异常。

    由 validate_payload 抛出，调用方可选择捕获或上抛。
    发布接口（publish_event）内部捕获此异常并记录 warning，不中断业务流程。
    """


class EventType(StrEnum):
    """统一事件类型枚举。

    继承 str + Enum 使其可直接 JSON 序列化为字符串，
    并支持与字符串字面量直接比较（EventType.TOOL_CALL_RUNNING == 'tool_call_running'）。
    """

    # === 工具调用生命周期事件（WebSocket 推送）===
    TOOL_CALL_PENDING = "tool_call_pending"  # 工具调用已创建，参数未就绪
    TOOL_CALL_WAITING = "tool_call_waiting"  # 同批次其他工具待审批，本工具等待中
    TOOL_CALL_RUNNING = "tool_call_running"  # 工具开始执行
    TOOL_CALL_COMPLETED = "tool_call_completed"  # 工具执行完成（成功）
    TOOL_CALL_FAILED = "tool_call_failed"  # 工具执行失败
    TOOL_CALL_TIMEOUT = "tool_call_timeout"  # 审批超时（终态）
    TOOL_CALL_REJECTED = "tool_call_rejected"  # 工具被用户拒绝（终态）

    # === 审批事件（WebSocket 推送）===
    APPROVAL_PENDING = "approval_pending"  # 审批请求已创建
    APPROVAL_PROCESSING = "approval_processing"  # 审批正在处理（用户已点击，后端处理中）
    APPROVAL_WAITING = "approval_waiting"  # 本审批已通过但同批次还有其他 pending（批量审批场景）
    APPROVAL_APPROVED = "approval_approved"  # 审批已通过
    APPROVAL_REJECTED = "approval_rejected"  # 审批已拒绝
    APPROVAL_TIMEOUT = "approval_timeout"  # 审批已超时

    # === 流式事件（SSE + WebSocket）===
    STREAM_STARTED = "stream_started"  # 流式会话已开始（通知非触发浏览器显示"正在思考"）
    STREAM_COMPLETED = "stream_completed"  # 流式会话已完成（通知所有浏览器更新终态）
    STREAM_FINALIZED = "stream_finalized"  # 流式输出已持久化（非请求浏览器可安全拉取后端数据）
    STREAM_INTERRUPTED = "stream_interrupted"  # 流被中断（深度研究模式：chat SSE 结束，Celery worker 仍在运行）
    STREAM_REASONING = "stream_reasoning"  # 推理过程
    STREAM_SOURCES = "stream_sources"  # 来源引用
    STREAM_SUGGESTIONS = "stream_suggestions"  # 建议
    STREAM_CONTEXT = "stream_context"  # 上下文
    STREAM_CONTENT_UPDATE = "stream_content_update"  # 内容更新（节流后的 chunk）
    STREAM_EVENT = "stream_event"  # 流式通用事件（approval/interrupted/model_fallback/research_task_id）

    # === 会话/消息事件（WebSocket 推送）===
    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"
    SESSION_DELETED = "session_deleted"
    MESSAGE_ADDED = "message_added"  # 新消息创建
    MESSAGE_UPDATED = "message_updated"  # 消息内容更新
    MESSAGE_DELETED = "message_deleted"  # 单条消息删除
    MESSAGES_DELETED = "messages_deleted"  # 批量消息删除
    MESSAGE_REGENERATED = "message_regenerated"  # 消息重新生成（版本归档 + 新版本切换）
    MESSAGE_REGENERATE_REVERTED = "message_regenerate_reverted"  # 重新生成回滚

    # === 任务事件（WebSocket 推送）===
    TASK_CREATED = "task_created"  # 新任务创建（深度研究/工作流），通知列表刷新
    TASK_STATUS_CHANGED = "task_status_changed"  # 任务状态进入终态（completed/failed），通知列表刷新
    TASK_PROGRESS = "task_progress"  # 后台任务进度（RAG 文档上传等），task 频道实时推送

    # === 学习工作流事件（WebSocket 推送）===
    WORKFLOW_STEP = "workflow_step"  # 工作流节点执行进度
    WORKFLOW_STATE_UPDATE = "workflow_state_update"  # 工作流状态变更
    WORKFLOW_COMPLETED = "workflow_completed"  # 工作流完成
    WORKFLOW_FAILED = "workflow_failed"  # 工作流失败

    @classmethod
    def from_value(cls, value: str) -> EventType | None:
        """从字符串值构造 EventType，无效时返回 None（不抛异常）。"""
        try:
            return cls(value)
        except ValueError:
            return None


class EventSource(StrEnum):
    """事件来源枚举。

    用于前端区分事件来源模块，路由到对应的 Store。
    v5 预留 WORKFLOW / AGENT，为未来新增模块零侵入支持。
    """

    CHAT = "chat"
    DEEP_RESEARCH = "deep_research"
    LEARNING = "learning"
    KNOWLEDGE = "knowledge"  # 知识库模块（RAG 文档上传等后台任务）
    # 预留：未来新增模块（_resolve_channels 默认路由到 session 频道）
    WORKFLOW = "workflow"
    AGENT = "agent"

    @classmethod
    def from_value(cls, value: str) -> EventSource | None:
        """从字符串值构造 EventSource，无效时返回 None。"""
        try:
            return cls(value)
        except ValueError:
            return None


class ToolCallLifecyclePayload(TypedDict, total=False):
    """工具调用生命周期事件 payload（统一 schema）。

    所有字段均为可选（total=False），但发布时调用方必须提供 tool_call_id、tool_name、
    source、source_id、message_id、parameters 六个核心字段，由 validate_payload 强制校验。

    注意：不包含 state 字段，状态由 event_type 本身表达。
    前端通过 event.type（ws_event_name）直接区分工具事件子类型
    （tool_call_pending / tool_call_waiting / tool_call_completed 等）。

    标准化（统一通用，三模块共享）：
    - tool_call_id 为唯一主键（= LLM AIMessage.tool_calls[].id）
    - source 为业务模块（CHAT/DEEP_RESEARCH/LEARNING），三模块共享同一 schema
    - source_id 为模块实例 ID（chat=session_id, deep_research=task_id, learning=thread_id）
    - session_id / task_id 为路由字段，由 RealtimeSyncService._resolve_channels 自动填充
    - cross_module_id 为跨模块同步目标 ID（仅 DEEP_RESEARCH 关联 chat 时为 chat_session_id）
    - parameters 必填（空参数必须传 {}），message_id 必填（未知时传 ''）
    - graph_interrupt_id 为批量审批批次 ID（同批次审批共享）

    子 agent 嵌套层级字段（Phase E3，与 ApprovalPayload 对齐）：
    工具调用生命周期事件也携带这些字段，确保非审批路径（SAFE 自动通过、
    子 agent 内部工具调用）的前端 ToolCallCard 也能展示完整调用链路。
    这些字段由 subagent_patch.py 注入到 configurable，经 ToolCallContext
    透传到 publish_tool_call，最终到达前端 toolCall 对象。
    """

    tool_call_id: str  # 工具调用 ID（= LLM tool_call.id，唯一主键，必填）
    tool_name: str  # 工具名称（必填）
    source: EventSource  # 事件来源（必填，= 业务模块）
    source_id: str  # 模块实例 ID（= session_id 或 task_id，必填）
    session_id: str | None  # 路由字段：chat/learning 场景的会话 ID
    task_id: str | None  # 路由字段：独立深度研究场景的任务 ID
    message_id: str  # 关联的消息 ID（必填，未知时传 ''）
    parameters: dict  # 工具输入参数（必填，空参数传 {}）
    result: Any | None  # 工具执行结果（COMPLETED 事件必填）
    error: str | None  # 错误信息（FAILED 事件必填）
    graph_interrupt_id: str | None  # 批量审批批次 ID（同批次审批共享）
    cross_module_id: str | None  # 跨模块同步目标 ID（DEEP_RESEARCH 关联 chat 时为 chat_session_id）
    auto_approved: bool | None  # SAFE 级自动通过标记（True=无需用户审批，仅审计）
    # 子 agent 嵌套层级字段（Phase E3，由 subagent_patch 注入到 configurable，
    # 经 ToolCallContext 透传到事件 payload，前端 ToolCallCard 展示完整调用链路）
    parent_tool_call_id: str | None  # 父工具调用 ID（主 agent 调用 task 工具的 tool_call_id）
    depth: int | None  # 嵌套层级（0=主 agent，1=一级子 agent）
    agent_name: str | None  # 子 agent 名称（如 web-researcher）
    agent_path: list | None  # 完整调用链路（如 ["main", "web-researcher"]）
    risk_ceiling: str | None  # 子 agent 角色风险上限（safe/controlled/high）
    risk_level: str | None  # 工具调用实际风险等级（safe/controlled/high，由 ApprovalMiddleware 计算，注入到 tool_call_* 事件 payload）


class ApprovalPayload(TypedDict, total=False):
    """审批事件 payload（统一 schema）。

    state 字段保留，与 event_type 1:1 对应（APPROVAL_PENDING → 'pending'），
    供前端 ApprovalStore 状态映射使用。
    前端通过 event.type（ws_event_name）直接区分审批事件子类型
    （approval_pending / approval_processing / approval_approved 等）。

    标准化（统一通用，三模块共享）：
    - tool_call_id 为唯一主键（= LLM tool_call.id）
    - interrupt_id = tool_call_id（保留以兼容前端历史代码）
    - source 为业务模块（CHAT/DEEP_RESEARCH/LEARNING）
    - source_id 为模块实例 ID（chat=session_id, deep_research=task_id, learning=thread_id）
    - cross_module_id 为跨模块同步目标 ID（仅 DEEP_RESEARCH 关联 chat 时为 chat_session_id）
    - parameters 必填（审批面板展示用），message_id 必填（未知时传 ''）
    - graph_interrupt_id 为批量审批批次 ID（同批次审批共享）
    """

    interrupt_id: str  # 中断 ID（= tool_call_id，必填）
    tool_call_id: str  # 工具调用 ID（= LLM tool_call.id，唯一主键，必填）
    tool_name: str  # 工具名称
    source: EventSource  # 事件来源（必填，= 业务模块）
    source_id: str  # 模块实例 ID（= session_id 或 task_id，必填）
    session_id: str | None  # 路由字段：chat/learning/关联研究场景的会话 ID
    task_id: str | None  # 路由字段：独立深度研究场景的任务 ID
    state: str  # 审批状态（pending/processing/approved/rejected/timeout，必填）
    parameters: dict  # 工具输入参数（必填，审批面板展示用，空参数传 {}）
    message_id: str  # 关联的消息 ID（必填，未知时传 ''）
    graph_interrupt_id: str | None  # 批量审批批次 ID（同批次审批共享）
    cross_module_id: str | None  # 跨模块同步目标 ID（DEEP_RESEARCH 关联 chat 时为 chat_session_id）
    operation: str | None  # 审批操作描述
    title: str | None  # 审批标题
    description: str | None  # 审批描述
    action: str | None  # 审批动作（execute/write 等）
    danger_level: str | None  # 危险等级（legacy: low/medium/high）
    risk_level: str | None  # 风险等级（新标准: safe/controlled/high，优先于 danger_level）
    # 子 agent 嵌套层级字段（Phase E3，由 ApprovalMiddleware 透传到 Approval.extra，
    # 审批事件 payload 也携带这些字段供前端展示完整调用链路）
    parent_tool_call_id: str | None  # 父工具调用 ID（主 agent 调用 task 工具的 tool_call_id）
    depth: int | None  # 嵌套层级（0=主 agent，1=一级子 agent）
    agent_name: str | None  # 子 agent 名称（如 web-researcher）
    agent_path: list | None  # 完整调用链路（如 ["main", "web-researcher"]）


class ToolCallRejectedPayload(TypedDict, total=False):
    """工具被用户拒绝事件 payload（终态）。

    复用 ToolCallLifecyclePayload 的核心字段，前端通过 event_type=tool_call_rejected
    识别该事件，将工具卡片状态更新为 rejected。

    标准化（统一通用，三模块共享）：
    - parameters 必填（空参数传 {}），message_id 必填（未知时传 ''）
    - cross_module_id 为跨模块同步目标 ID（DEEP_RESEARCH 关联 chat 时为 chat_session_id）
    """

    tool_call_id: str  # 工具调用 ID（= LLM tool_call.id，唯一主键，必填）
    tool_name: str  # 工具名称（必填）
    source: EventSource  # 事件来源（必填，= 业务模块）
    source_id: str  # 模块实例 ID（= session_id 或 task_id，必填）
    session_id: str | None  # 路由字段：chat/learning 场景的会话 ID
    task_id: str | None  # 路由字段：独立深度研究场景的任务 ID
    message_id: str  # 关联的消息 ID（必填，未知时传 ''）
    parameters: dict  # 工具输入参数（必填，空参数传 {}）
    graph_interrupt_id: str | None  # 批量审批批次 ID（同批次审批共享）
    cross_module_id: str | None  # 跨模块同步目标 ID（DEEP_RESEARCH 关联 chat 时为 chat_session_id）


class StreamPayload(TypedDict, total=False):
    """流式事件 payload（统一 schema）。

    session_id / task_id 为路由字段，二选一
    """

    message_id: str | None  # 关联的消息 ID
    source: EventSource  # 事件来源（必填）
    source_id: str  # 源实体 ID（必填）
    session_id: str | None  # 聊天会话 ID（chat/learning 场景路由用）
    task_id: str | None  # 研究任务 ID（独立深度研究场景路由用）
    data: dict  # 流式数据（reasoning/sources/suggestions/context/content）
    seq: int | None  # 业务序列号（幂等保护，可选）


class TaskProgressPayload(TypedDict, total=False):
    """后台任务进度事件 payload（统一 schema）。

    供 RAG 文档上传、索引重建等耗时任务在 task:{task_id} 频道实时推送进度，
    前端据此展示进度条并识别终态（status=success / failure）。

    路由字段 task_id 由 _publish_to_task_async 注入事件顶层，payload 中不强制携带。
    """

    task_id: str | None  # 业务任务 ID（与 WebSocket 频道路由 ID 一致）
    source: EventSource  # 事件来源（必填，如 knowledge）
    source_id: str | None  # 源实体 ID（RAG 场景为 user_index_name）
    status: str | None  # 任务状态（started/progress/success/failure）
    progress: int | None  # 进度百分比 0-100
    current_step: str | None  # 当前步骤描述（加载文档/分块/向量化）
    result: dict | None  # 成功结果（documents_uploaded/chunks_created/files）
    error: str | None  # 失败原因（status=failure 时）


# === 事件类型到 payload 类型的映射 ===

_PAYLOAD_TYPE_MAP: dict[EventType, type] = {
    EventType.TOOL_CALL_PENDING: ToolCallLifecyclePayload,
    EventType.TOOL_CALL_WAITING: ToolCallLifecyclePayload,
    EventType.TOOL_CALL_RUNNING: ToolCallLifecyclePayload,
    EventType.TOOL_CALL_COMPLETED: ToolCallLifecyclePayload,
    EventType.TOOL_CALL_FAILED: ToolCallLifecyclePayload,
    EventType.TOOL_CALL_TIMEOUT: ToolCallLifecyclePayload,
    EventType.TOOL_CALL_REJECTED: ToolCallRejectedPayload,
    EventType.APPROVAL_PENDING: ApprovalPayload,
    EventType.APPROVAL_PROCESSING: ApprovalPayload,
    EventType.APPROVAL_WAITING: ApprovalPayload,
    EventType.APPROVAL_APPROVED: ApprovalPayload,
    EventType.APPROVAL_REJECTED: ApprovalPayload,
    EventType.APPROVAL_TIMEOUT: ApprovalPayload,
    EventType.STREAM_REASONING: StreamPayload,
    EventType.STREAM_SOURCES: StreamPayload,
    EventType.STREAM_SUGGESTIONS: StreamPayload,
    EventType.STREAM_CONTEXT: StreamPayload,
    EventType.STREAM_CONTENT_UPDATE: StreamPayload,
    EventType.STREAM_INTERRUPTED: StreamPayload,
    EventType.TASK_PROGRESS: TaskProgressPayload,
    # STREAM_STARTED / STREAM_COMPLETED / SESSION_* / MESSAGE_* 事件无固定 payload schema，校验时跳过必填字段检查
}

# === 各事件类型的必填字段（用于 validate_payload 强制校验）===

_REQUIRED_FIELDS: dict[EventType, tuple[str, ...]] = {
    # 工具调用生命周期事件：5 个核心字段必填（三模块共享）
    # parameters 必填（空参数传 {}），message_id 可选（learning 模块无 chat message）
    # session_id/task_id 二选一路由，由 RealtimeSyncService._resolve_channels 自动填充，不在必填校验中
    EventType.TOOL_CALL_PENDING: ("tool_call_id", "tool_name", "source", "source_id", "parameters"),
    EventType.TOOL_CALL_WAITING: ("tool_call_id", "tool_name", "source", "source_id", "parameters"),
    EventType.TOOL_CALL_RUNNING: ("tool_call_id", "tool_name", "source", "source_id", "parameters"),
    EventType.TOOL_CALL_COMPLETED: ("tool_call_id", "tool_name", "source", "source_id", "parameters"),
    EventType.TOOL_CALL_FAILED: ("tool_call_id", "tool_name", "source", "source_id", "parameters", "error"),
    EventType.TOOL_CALL_TIMEOUT: ("tool_call_id", "tool_name", "source", "source_id", "parameters"),
    EventType.TOOL_CALL_REJECTED: ("tool_call_id", "tool_name", "source", "source_id", "parameters"),
    # 审批事件：6 个核心字段必填（三模块共享）
    # parameters 必填（审批面板展示用），message_id 可选（learning 模块无 chat message）
    EventType.APPROVAL_PENDING: ("interrupt_id", "tool_call_id", "source", "source_id", "state", "parameters"),
    EventType.APPROVAL_PROCESSING: ("interrupt_id", "tool_call_id", "source", "source_id", "state", "parameters"),
    EventType.APPROVAL_WAITING: ("interrupt_id", "tool_call_id", "source", "source_id", "state", "parameters"),
    EventType.APPROVAL_APPROVED: ("interrupt_id", "tool_call_id", "source", "source_id", "state", "parameters"),
    EventType.APPROVAL_REJECTED: ("interrupt_id", "tool_call_id", "source", "source_id", "state", "parameters"),
    EventType.APPROVAL_TIMEOUT: ("interrupt_id", "tool_call_id", "source", "source_id", "state", "parameters"),
    # 流式事件：source + source_id 必填，data 必填（session_id/task_id 二选一路由）
    EventType.STREAM_REASONING: ("source", "source_id", "data"),
    EventType.STREAM_SOURCES: ("source", "source_id", "data"),
    EventType.STREAM_SUGGESTIONS: ("source", "source_id", "data"),
    EventType.STREAM_CONTEXT: ("source", "source_id", "data"),
    EventType.STREAM_CONTENT_UPDATE: ("source", "source_id", "data"),
    EventType.STREAM_INTERRUPTED: ("source", "source_id", "data"),
    # STREAM_STARTED / STREAM_COMPLETED / STREAM_FINALIZED：source + source_id 必填（无 data 字段）
    EventType.STREAM_STARTED: ("source", "source_id"),
    EventType.STREAM_COMPLETED: ("source", "source_id"),
    EventType.STREAM_FINALIZED: ("source", "source_id"),
    # SESSION_* / MESSAGE_* 无必填字段
}

# === 事件类型到 WebSocket 频道事件名的映射 ===
# 每个 EventType 使用其 value 作为独立 ws_event_name，
# 前端通过 event.type 直接区分事件子类型（不再统一映射为 tool_call_changed / approval_changed）。
# - 工具事件 7 个独立：tool_call_pending / tool_call_waiting / ...
# - 审批事件 5 个独立：approval_pending / approval_processing / ...
# - 流式事件保持原名：stream_event（reasoning/sources/suggestions/context/content_update）
#   / stream_started / stream_completed / stream_finalized
# - 会话/消息事件保持原名

_WS_EVENT_NAME_MAP: dict[EventType, str] = {
    EventType.TOOL_CALL_PENDING: "tool_call_pending",
    EventType.TOOL_CALL_WAITING: "tool_call_waiting",
    EventType.TOOL_CALL_RUNNING: "tool_call_running",
    EventType.TOOL_CALL_COMPLETED: "tool_call_completed",
    EventType.TOOL_CALL_FAILED: "tool_call_failed",
    EventType.TOOL_CALL_TIMEOUT: "tool_call_timeout",
    EventType.TOOL_CALL_REJECTED: "tool_call_rejected",
    EventType.APPROVAL_PENDING: "approval_pending",
    EventType.APPROVAL_PROCESSING: "approval_processing",
    EventType.APPROVAL_WAITING: "approval_waiting",
    EventType.APPROVAL_APPROVED: "approval_approved",
    EventType.APPROVAL_REJECTED: "approval_rejected",
    EventType.APPROVAL_TIMEOUT: "approval_timeout",
    EventType.STREAM_REASONING: "stream_event",
    EventType.STREAM_SOURCES: "stream_event",
    EventType.STREAM_SUGGESTIONS: "stream_event",
    EventType.STREAM_CONTEXT: "stream_event",
    EventType.STREAM_CONTENT_UPDATE: "stream_event",
    EventType.STREAM_STARTED: "stream_started",
    EventType.STREAM_COMPLETED: "stream_completed",
    EventType.STREAM_FINALIZED: "stream_finalized",
    EventType.STREAM_INTERRUPTED: "stream_interrupted",
    EventType.SESSION_CREATED: "session_created",
    EventType.SESSION_UPDATED: "session_updated",
    EventType.SESSION_DELETED: "session_deleted",
    EventType.MESSAGE_ADDED: "message_added",
    EventType.MESSAGE_UPDATED: "message_updated",
    EventType.MESSAGE_DELETED: "message_deleted",
    EventType.MESSAGES_DELETED: "messages_deleted",
    EventType.MESSAGE_REGENERATED: "message_regenerated",
    EventType.MESSAGE_REGENERATE_REVERTED: "message_regenerate_reverted",
    EventType.WORKFLOW_STEP: "workflow_step",
    EventType.WORKFLOW_STATE_UPDATE: "workflow_state_update",
    EventType.WORKFLOW_COMPLETED: "workflow_completed",
    EventType.WORKFLOW_FAILED: "workflow_failed",
    EventType.TASK_PROGRESS: "task_progress",
}


def get_ws_event_name(event_type: EventType) -> str:
    """获取事件类型对应的 WebSocket 频道事件名。

    每个 EventType 使用其 value 作为独立 ws_event_name，
    前端通过 event.type 直接区分事件子类型。

    Args:
        event_type: EventType 枚举值

    Returns:
        str: WebSocket 频道事件名（如 'tool_call_running' / 'approval_pending'）
    """
    return _WS_EVENT_NAME_MAP.get(event_type, event_type.value)


def validate_payload(event_type: EventType, payload: dict) -> None:
    """校验事件 payload 是否符合 Schema 定义。

    校验规则：
    1. event_type 必须是 EventType 枚举值
    2. payload 必须是 dict
    3. 必填字段必须存在且非 None（空字符串/空 dict 视为有效）
    4. source 字段必须是 EventSource 枚举值或合法字符串
    5. parameters 字段必须是 dict（工具/审批事件必填）
    6. graph_interrupt_id / cross_module_id 可选，但非空时必须是字符串

    Args:
        event_type: 事件类型枚举
        payload: 事件 payload dict

    Raises:
        PayloadValidationError: 校验失败时抛出，包含缺失字段列表
    """
    if not isinstance(event_type, EventType):
        raise PayloadValidationError(
            f"event_type 必须是 EventType 枚举值，收到: {type(event_type).__name__}={event_type!r}"
        )

    if not isinstance(payload, dict):
        raise PayloadValidationError(f"payload 必须是 dict，收到: {type(payload).__name__}")

    required = _REQUIRED_FIELDS.get(event_type)
    if not required:
        # SESSION_* / MESSAGE_* 事件无必填字段，跳过校验
        return

    missing = [field for field in required if field not in payload or payload[field] is None]
    if missing:
        raise PayloadValidationError(
            f"事件 {event_type.value} payload 缺少必填字段: {missing}, 实际 payload keys: {list(payload.keys())}"
        )

    # source 字段必须是合法的 EventSource
    source_value = payload.get("source")
    if source_value is not None and not isinstance(source_value, EventSource):
        if EventSource.from_value(str(source_value)) is None:
            raise PayloadValidationError(
                f"source 字段必须是 EventSource 枚举值或合法字符串"
                f"（chat/deep_research/learning），收到: {source_value!r}"
            )

    # parameters 字段必须是 dict（工具/审批事件必填）
    parameters_value = payload.get("parameters")
    if parameters_value is not None and not isinstance(parameters_value, dict):
        raise PayloadValidationError(
            f"parameters 字段必须是 dict，收到: {type(parameters_value).__name__}={parameters_value!r}"
        )

    # graph_interrupt_id / cross_module_id 可选，但非空时必须是字符串
    for optional_str_field in ("graph_interrupt_id", "cross_module_id"):
        val = payload.get(optional_str_field)
        if val is not None and not isinstance(val, str):
            raise PayloadValidationError(
                f"{optional_str_field} 字段必须是字符串或 None，收到: {type(val).__name__}={val!r}"
            )
        if val is not None and not val:
            # 空字符串视为未设置，清除以避免歧义
            payload[optional_str_field] = None


def is_tool_lifecycle_event(event_type: EventType) -> bool:
    """判断是否为工具调用生命周期事件。"""
    payload_type = _PAYLOAD_TYPE_MAP.get(event_type)
    return payload_type in (ToolCallLifecyclePayload, ToolCallRejectedPayload)


def is_approval_event(event_type: EventType) -> bool:
    """判断是否为审批事件。"""
    return event_type in _PAYLOAD_TYPE_MAP and _PAYLOAD_TYPE_MAP[event_type] is ApprovalPayload


def is_stream_event(event_type: EventType) -> bool:
    """判断是否为流式事件。"""
    return event_type in _PAYLOAD_TYPE_MAP and _PAYLOAD_TYPE_MAP[event_type] is StreamPayload
