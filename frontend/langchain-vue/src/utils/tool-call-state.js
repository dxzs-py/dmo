/**
 * 工具调用状态合并矩阵与事件去重工具（三模块共享）。
 *
 * 设计目的：
 * - 消除"工具状态"与"审批状态"的矛盾组合（如 tool=running + approval=rejected）
 * - 统一 chat / deep_research / learning 三模块的事件去重逻辑
 *
 * 状态合并规则（优先级从高到低）：
 *   1. approval 终态（timeout/rejected）优先级最高，覆盖任何 tool 状态
 *   2. tool=completed 时，无论 approval 状态如何，均为 completed（工具实际完成，不可回退）
 *   3. tool=running + approval=pending → waiting（修复问题 I/P：审批未通过时显示"等待中"而非"执行中"）
 *   4. tool=running + approval=rejected → rejected（矛盾时取审批终态）
 *   5. 其余组合取 tool 状态（approval 非终态时不影响工具显示）
 *
 * 去重规则：
 *   - 基于 `${tool_call_id}:${event_type}` 作为 key，seq 作为版本号
 *   - 同 key 的旧 seq 事件被丢弃（seq <= lastSeq）
 *   - INPUT_READY / COMPLETED / RUNNING 事件允许重发（重新生成场景需要重新发布）
 *   - dedupMap 容量上限 5000，超限时淘汰最早的 1000 条
 */

import { ToolCallStatus, ApprovalState } from '@/types'

/**
 * 状态合并矩阵：MERGE_MATRIX[toolStatus][approvalState] => effectiveStatus
 *
 * 行 = ToolCallStatus，列 = ApprovalState
 * undefined/null 列表示 approval 状态缺失（无审批流程的工具）
 *
 * 修复问题 I/P：RUNNING + PENDING → WAITING
 *   原因：审批未通过时工具实际未执行，显示"等待中"更准确
 *   后端在审批通过时（state=PROCESSING）发布 TOOL_CALL_RUNNING，
 *   所以前端收到 RUNNING 事件时 approval.state 应该已经是 PROCESSING/APPROVED，
 *   如果仍是 PENDING 说明事件时序异常，降级显示为 WAITING 更安全。
 */
const MERGE_MATRIX = {
  [ToolCallStatus.PENDING]: {
    [ApprovalState.PENDING]: ToolCallStatus.PENDING,
    [ApprovalState.PROCESSING]: ToolCallStatus.PENDING,
    [ApprovalState.WAITING]: ToolCallStatus.WAITING,
    [ApprovalState.APPROVED]: ToolCallStatus.PENDING,
    [ApprovalState.REJECTED]: ToolCallStatus.REJECTED,
    [ApprovalState.TIMEOUT]: ToolCallStatus.TIMEOUT,
    undefined: ToolCallStatus.PENDING,
    null: ToolCallStatus.PENDING,
  },
  [ToolCallStatus.WAITING]: {
    [ApprovalState.PENDING]: ToolCallStatus.WAITING,
    [ApprovalState.PROCESSING]: ToolCallStatus.WAITING,
    [ApprovalState.WAITING]: ToolCallStatus.WAITING,
    [ApprovalState.APPROVED]: ToolCallStatus.WAITING,
    [ApprovalState.REJECTED]: ToolCallStatus.REJECTED,
    [ApprovalState.TIMEOUT]: ToolCallStatus.TIMEOUT,
    undefined: ToolCallStatus.WAITING,
    null: ToolCallStatus.WAITING,
  },
  [ToolCallStatus.RUNNING]: {
    // 修复问题 I/P：审批未通过时（PENDING）显示"等待中"而非"执行中"
    [ApprovalState.PENDING]: ToolCallStatus.WAITING,
    [ApprovalState.PROCESSING]: ToolCallStatus.RUNNING,
    [ApprovalState.WAITING]: ToolCallStatus.WAITING,
    [ApprovalState.APPROVED]: ToolCallStatus.RUNNING,
    [ApprovalState.REJECTED]: ToolCallStatus.REJECTED,
    [ApprovalState.TIMEOUT]: ToolCallStatus.TIMEOUT,
    undefined: ToolCallStatus.RUNNING,
    null: ToolCallStatus.RUNNING,
  },
  [ToolCallStatus.COMPLETED]: {
    [ApprovalState.PENDING]: ToolCallStatus.COMPLETED,
    [ApprovalState.PROCESSING]: ToolCallStatus.COMPLETED,
    [ApprovalState.WAITING]: ToolCallStatus.COMPLETED,
    [ApprovalState.APPROVED]: ToolCallStatus.COMPLETED,
    [ApprovalState.REJECTED]: ToolCallStatus.COMPLETED,
    [ApprovalState.TIMEOUT]: ToolCallStatus.COMPLETED,
    undefined: ToolCallStatus.COMPLETED,
    null: ToolCallStatus.COMPLETED,
  },
  [ToolCallStatus.FAILED]: {
    [ApprovalState.PENDING]: ToolCallStatus.FAILED,
    [ApprovalState.PROCESSING]: ToolCallStatus.FAILED,
    [ApprovalState.WAITING]: ToolCallStatus.FAILED,
    [ApprovalState.APPROVED]: ToolCallStatus.FAILED,
    [ApprovalState.REJECTED]: ToolCallStatus.REJECTED,
    [ApprovalState.TIMEOUT]: ToolCallStatus.TIMEOUT,
    undefined: ToolCallStatus.FAILED,
    null: ToolCallStatus.FAILED,
  },
  [ToolCallStatus.TIMEOUT]: {
    [ApprovalState.PENDING]: ToolCallStatus.TIMEOUT,
    [ApprovalState.APPROVED]: ToolCallStatus.TIMEOUT,
    [ApprovalState.REJECTED]: ToolCallStatus.REJECTED,
    [ApprovalState.TIMEOUT]: ToolCallStatus.TIMEOUT,
    undefined: ToolCallStatus.TIMEOUT,
    null: ToolCallStatus.TIMEOUT,
  },
  [ToolCallStatus.REJECTED]: {
    [ApprovalState.PENDING]: ToolCallStatus.REJECTED,
    [ApprovalState.APPROVED]: ToolCallStatus.REJECTED,
    [ApprovalState.REJECTED]: ToolCallStatus.REJECTED,
    [ApprovalState.TIMEOUT]: ToolCallStatus.REJECTED,
    undefined: ToolCallStatus.REJECTED,
    null: ToolCallStatus.REJECTED,
  },
}

/**
 * 工具终态集合：进入这些状态后隐藏审批徽章，避免"已完成 + 待审批"等矛盾组合。
 */
const TOOL_TERMINAL_STATES = new Set([
  ToolCallStatus.COMPLETED,
  ToolCallStatus.FAILED,
  ToolCallStatus.TIMEOUT,
  ToolCallStatus.REJECTED,
])

/**
 * 计算工具调用的有效显示状态（合并 tool 状态与 approval 状态）。
 *
 * M11：返回二元组 `{ status, approvalBadge }`：
 * - `status`：合并后的 ToolCallStatus 枚举值（用于工具图标/文本/颜色）
 * - `approvalBadge`：合并后的审批徽章状态（用于审批辅助标签）；
 *   工具终态（completed/failed/timeout/rejected）时强制为 null，避免矛盾组合；
 *   非终态时等于 approvalState（可能为 null 表示无审批流程）
 *
 * 注意：ToolCallCard.vue 中 approvalStateLabel 已改为直接使用 approvalData.state，
 * 不再依赖此处的 approvalBadge（修复问题 U：审批通过后保留"已确认"徽章）。
 * 此处保留 approvalBadge 仅为兼容其他可能引用的组件。
 *
 * @param {string} toolStatus - ToolCallStatus 枚举值
 * @param {string|null|undefined} approvalState - ApprovalState 枚举值或 null/undefined
 * @returns {{status: string, approvalBadge: string|null}} 合并后的二元组
 */
export function computeEffectiveState(toolStatus, approvalState) {
  const toolRow = MERGE_MATRIX[toolStatus] || MERGE_MATRIX[ToolCallStatus.PENDING]
  const status = toolRow[approvalState] ?? toolRow[undefined] ?? toolStatus
  const approvalBadge = TOOL_TERMINAL_STATES.has(status) ? null : (approvalState || null)
  return { status, approvalBadge }
}

/**
 * 事件去重 Map：key = `${tool_call_id}:${event_type}`，value = lastSeq
 * 容量上限 _DEDUP_MAX_SIZE，超限时淘汰最早的 1000 条
 */
const _dedupMap = new Map()
const _DEDUP_MAX_SIZE = 5000

/**
 * 允许重发的事件类型集合（重新生成场景需要重新发布）
 * - INPUT_READY：参数补全场景下重发
 * - COMPLETED：重新生成场景下需要重新发布完成事件
 * - RUNNING：审批通过时由 approval_service 发布，可能因并发多次发布
 */
const _RESENDABLE_EVENT_TYPES = new Set([
  'tool_call_input_ready',
  'tool_call_completed',
  'tool_call_running',
])

/**
 * 判断事件是否为重复事件（基于 tool_call_id + event_type + seq 去重）。
 *
 * @param {string} toolCallId - 工具调用唯一 ID
 * @param {string} eventType - 事件类型（如 'tool_call_running'）
 * @param {number|string} seq - 事件序号（后端注入，单调递增）
 * @param {boolean} allowResend - 是否允许重发（已废弃，内部使用 _RESENDABLE_EVENT_TYPES）
 * @returns {boolean} true 表示重复事件应丢弃，false 表示新事件应处理
 */
export function isDuplicateEvent(toolCallId, eventType, seq, allowResend = false) {
  if (!seq || !toolCallId || !eventType) return false
  const key = `${toolCallId}:${eventType}`
  const lastSeq = _dedupMap.get(key)
  if (lastSeq !== undefined && seq <= lastSeq) {
    // 允许重发的事件类型或调用方显式允许重发时，不丢弃
    if (!allowResend && !_RESENDABLE_EVENT_TYPES.has(eventType)) return true
  }
  _dedupMap.set(key, seq)
  // 容量保护：超限时淘汰最早的 1000 条
  if (_dedupMap.size > _DEDUP_MAX_SIZE) {
    const keysToDelete = Array.from(_dedupMap.keys()).slice(0, 1000)
    keysToDelete.forEach(k => _dedupMap.delete(k))
  }
  return false
}

/**
 * 清空去重 Map（用于会话切换或用户登出时重置状态）。
 */
export function clearDedupMap() {
  _dedupMap.clear()
}