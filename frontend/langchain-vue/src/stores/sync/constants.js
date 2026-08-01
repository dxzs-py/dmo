import { ToolCallStatus, ApprovalState } from '@/types'

/**
 * 事件类型常量（与后端 event_schema.py EventType 枚举对应）
 * 每个 EventType 使用其 value 作为 ws_event_name，前端通过 event.type 直接区分 12 个事件类型。
 * 后端 Task 16+17 已改为每个 EventType 独立 ws_event_name，不再使用统一的 'tool_call_changed' / 'approval_changed'。
 */

/** 工具调用事件类型 → ToolCallStatus 映射 */
export const TOOL_CALL_STATUS_MAP = {
  tool_call_pending: ToolCallStatus.PENDING,
  tool_call_input_ready: ToolCallStatus.PENDING,
  tool_call_waiting: ToolCallStatus.WAITING,
  tool_call_pending_approval: ToolCallStatus.PENDING_APPROVAL,
  tool_call_approved: ToolCallStatus.APPROVED,
  tool_call_rejected: ToolCallStatus.REJECTED,
  tool_call_running: ToolCallStatus.RUNNING,
  tool_call_completed: ToolCallStatus.COMPLETED,
  tool_call_failed: ToolCallStatus.FAILED,
  tool_call_timeout: ToolCallStatus.TIMEOUT,
}

/** 审批事件类型 → ApprovalState 映射 */
export const APPROVAL_STATE_MAP = {
  approval_pending: ApprovalState.PENDING,
  approval_processing: ApprovalState.PROCESSING,
  approval_waiting: ApprovalState.WAITING,
  approval_approved: ApprovalState.APPROVED,
  approval_rejected: ApprovalState.REJECTED,
  approval_timeout: ApprovalState.TIMEOUT,
}

/** 工具调用结果事件集合（映射到 updateOrAddToolResult 路径） */
export const TOOL_CALL_RESULT_STATUSES = new Set([
  ToolCallStatus.COMPLETED,
  ToolCallStatus.FAILED,
  ToolCallStatus.TIMEOUT,
  ToolCallStatus.REJECTED,
])
