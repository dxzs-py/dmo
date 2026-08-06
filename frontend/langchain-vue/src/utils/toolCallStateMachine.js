/**
 * 前端工具调用状态机（唯一权威）
 *
 * 本模块是工具调用状态的唯一状态机，收敛前端所有分散的状态推导/演进
 * （实时同步核心层权威化，Task 2）。所有工具状态变更必须经过
 * applyToolCallState 唯一应用入口，禁止在其他文件自行推导/覆盖状态。
 *
 * 包含：
 *   - 状态域常量：pending / waiting / running / completed / failed / timeout（含 rejected）
 *   - 事件态常量：input-available / input-error / output-available / output-error / interrupt
 *   - 合法转换表 + canTransition：对应后端 tool_call_lifecycle.py 的 _VALID_TRANSITIONS
 *   - 唯一状态应用入口 applyToolCallState：校验合法转换、终态不回退、返回新状态
 *   - 展示状态推导 deriveDisplayStatus：组件读取展示状态（无分散三元推导）
 *
 * 层次关系：
 *   - 后端 tool_call_lifecycle.py：权威状态机，验证转换并发布事件
 *   - 前端本模块：防御层，处理事件乱序 / 丢失 / 快照校对
 *
 * 转换规则（对齐后端 _VALID_TRANSITIONS）：
 *   PENDING   → WAITING / RUNNING / COMPLETED / FAILED / TIMEOUT / REJECTED (+ self)
 *   WAITING   → RUNNING / COMPLETED / FAILED / TIMEOUT / REJECTED (+ self)
 *   RUNNING   → COMPLETED / FAILED / TIMEOUT (+ self)
 *   终态（COMPLETED / FAILED / TIMEOUT / REJECTED）→ 不可再转换
 */

import { ToolCallStatus, ApprovalState } from '../types/index.js'

// ==================== 状态域常量（唯一权威，复用 types 的 ToolCallStatus） ====================

export { ToolCallStatus }

// ==================== 事件态常量 ====================

/**
 * 工具调用事件态（toolCall.state 字段，对应后端 SSE tool 事件 / stream 持久化状态）
 *
 * 其中 input-error / interrupt 为预留事件态（后端当前不发布），
 * 映射为语义等价状态以保证事件态完整覆盖。
 */
export const TOOL_CALL_EVENT_STATES = {
  INPUT_AVAILABLE: 'input-available',
  INPUT_ERROR: 'input-error',
  OUTPUT_AVAILABLE: 'output-available',
  OUTPUT_ERROR: 'output-error',
  INTERRUPT: 'interrupt',
}

/**
 * 事件态 → 状态域映射（对齐后端 stream_chunk_processors._STATE_TO_STATUS：
 * input-available→pending / output-available→completed / output-error→failed）
 */
export const EVENT_STATE_TO_STATUS = {
  [TOOL_CALL_EVENT_STATES.INPUT_AVAILABLE]: ToolCallStatus.PENDING,
  [TOOL_CALL_EVENT_STATES.INPUT_ERROR]: ToolCallStatus.FAILED,
  [TOOL_CALL_EVENT_STATES.OUTPUT_AVAILABLE]: ToolCallStatus.COMPLETED,
  [TOOL_CALL_EVENT_STATES.OUTPUT_ERROR]: ToolCallStatus.FAILED,
  [TOOL_CALL_EVENT_STATES.INTERRUPT]: ToolCallStatus.WAITING,
}

// ==================== 转换表（对齐后端 _VALID_TRANSITIONS） ====================

/** @type {Record<string, Set<string>>} */
const _TRANSITION_MAP = {
  [ToolCallStatus.PENDING]: new Set([
    ToolCallStatus.WAITING,
    ToolCallStatus.RUNNING,
    ToolCallStatus.COMPLETED,
    ToolCallStatus.FAILED,
    ToolCallStatus.TIMEOUT,
    // REJECTED 允许从 PENDING 转换（对齐后端 _VALID_TRANSITIONS：
    // TOOL_CALL_REJECTED 前驱包含 PENDING/WAITING/None）
    ToolCallStatus.REJECTED,
  ]),
  [ToolCallStatus.WAITING]: new Set([
    ToolCallStatus.RUNNING,
    ToolCallStatus.COMPLETED,
    ToolCallStatus.FAILED,
    ToolCallStatus.TIMEOUT,
    ToolCallStatus.REJECTED,
  ]),
  [ToolCallStatus.RUNNING]: new Set([
    ToolCallStatus.COMPLETED,
    ToolCallStatus.FAILED,
    ToolCallStatus.TIMEOUT,
  ]),
  [ToolCallStatus.COMPLETED]: new Set(),
  [ToolCallStatus.FAILED]: new Set(),
  [ToolCallStatus.TIMEOUT]: new Set(),
  [ToolCallStatus.REJECTED]: new Set(),
}

// ==================== 终态 / 非终态 / 受保护集合 ====================

/**
 * 工具调用终态（不可再转换）——唯一权威集合
 * stores/sync/constants.js 的 TOOL_CALL_RESULT_STATUSES、
 * composables/useSnapshotSync.js 的快照终态判断均以此处为准。
 */
export const TERMINAL_STATUSES = new Set([
  ToolCallStatus.COMPLETED,
  ToolCallStatus.FAILED,
  ToolCallStatus.TIMEOUT,
  ToolCallStatus.REJECTED,
])

/**
 * 非终态状态集合
 * 用途：流式完成时兜底修正仍处于非终态的工具调用
 * （stores/sync/messageIntegrity.js finalizeToolCallsForCompletedMessage）。
 */
export const NON_TERMINAL_STATUSES = new Set([
  ToolCallStatus.PENDING,
  ToolCallStatus.RUNNING,
  ToolCallStatus.WAITING,
])

/**
 * 受保护状态集合（终态 + WAITING）
 * 保留导出供既有调用方使用；状态推进校验统一走 canTransition / applyToolCallState。
 */
export const PROTECTED_STATUSES = new Set([
  ToolCallStatus.COMPLETED,
  ToolCallStatus.FAILED,
  ToolCallStatus.TIMEOUT,
  ToolCallStatus.REJECTED,
  ToolCallStatus.WAITING,
])

// ==================== 公共 API ====================

/**
 * 终态判定
 * @param {string} status
 * @returns {boolean}
 */
export function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(status)
}

/**
 * 状态转换合法性检查
 *
 * 规则：
 *   1. 同状态允许（幂等）
 *   2. 终态不可转换到任何状态
 *   3. 查询 _TRANSITION_MAP
 *
 * @param {string} fromStatus - 当前状态
 * @param {string} toStatus - 目标状态
 * @returns {boolean} 是否允许转换
 */
export function canTransition(fromStatus, toStatus) {
  // 同状态允许（幂等）
  if (fromStatus === toStatus) return true
  // 终态不可逆
  if (TERMINAL_STATUSES.has(fromStatus)) return false
  // 查询转换映射
  const allowed = _TRANSITION_MAP[fromStatus]
  return allowed ? allowed.has(toStatus) : false
}

/**
 * 工具调用状态优先级（数值越大优先级越高）
 *
 * 用于快照校对：当后端状态优先级 > 本地时，以后端为准。
 *
 * @param {string} status
 * @returns {number}
 */
export function getToolCallStatusPriority(status) {
  switch (status) {
    case ToolCallStatus.PENDING: return 0
    case ToolCallStatus.WAITING: return 1
    case ToolCallStatus.RUNNING: return 2
    case ToolCallStatus.COMPLETED: return 3
    case ToolCallStatus.FAILED: return 3
    case ToolCallStatus.TIMEOUT: return 3
    case ToolCallStatus.REJECTED: return 3
    default: return 0
  }
}

/**
 * 从事件数据推导目标状态（唯一推导逻辑）
 *
 * 优先级：
 *   1. 显式 status
 *   2. 事件态 state 映射（EVENT_STATE_TO_STATUS）
 *   3. result → COMPLETED（仅 result 模式，且 state 非 output-error）
 *   4. error → FAILED（仅 result 模式）
 *
 * mode 语义：
 *   - 'add'：工具创建/中间态路径（对应 SSE tool / WS tool_call_pending 等），
 *     仅按显式 status 与事件态映射推导，不从 result/error 推导；
 *     state 存在但未命中映射表时兜底 pending（对齐后端 _STATE_TO_STATUS 的未知态兜底）。
 *   - 'result'：工具结果路径（对应 SSE tool_result / WS tool_call_completed 等），
 *     允许 result/error 推导。
 *
 * @param {Object} data - 事件数据
 * @param {'add'|'result'} mode - 推导模式
 * @param {string} [fallback] - 无任何可推导字段时的兜底状态；缺省 undefined（不设置 status）
 * @returns {string|undefined}
 */
function _deriveTargetStatus(data, mode, fallback) {
  if (data.status !== undefined && data.status !== null && data.status !== '') {
    return data.status
  }
  if (data.state !== undefined) {
    const mapped = EVENT_STATE_TO_STATUS[data.state]
    if (mapped !== undefined) return mapped
    // 事件态存在但未命中映射表（未知事件态）：add 路径对齐后端未知态兜底 pending
    if (mode === 'add') return ToolCallStatus.PENDING
    // result 路径：继续按 result/error 推导
  }
  if (mode === 'result') {
    if (data.result != null && data.state !== TOOL_CALL_EVENT_STATES.OUTPUT_ERROR) {
      return ToolCallStatus.COMPLETED
    }
    if (data.error != null) return ToolCallStatus.FAILED
  }
  return fallback
}

/**
 * 唯一状态应用入口（核心层权威）
 *
 * 供 addOrUpdateToolCallInMap / updateOrAddToolResultInMap / 快照恢复 / 消息完整性兜底
 * 统一调用。任何工具状态变更必须经过本函数，禁止调用方自行推导/覆盖状态。
 *
 * 规则：
 *   1. 目标状态推导（_deriveTargetStatus，按 mode 区分 add/result 路径）
 *   2. 新建（existing 为空）：直接应用推导结果
 *   3. 终态不回退：existing 为终态时目标状态一律不生效
 *   4. 合法转换校验（canTransition）：非法转换拒绝，保持 existing.status
 *   5. 同状态视为合法（幂等，applied=true 但状态不变）
 *
 * 注意：本函数为纯函数，不修改 existing / updates；调用方需按返回的 status 写回。
 *
 * @param {Object|null} existing - 现有 toolCall（null 表示新建）
 * @param {Object} updates - 事件数据（status/state/result/error 等字段）
 * @param {Object} [options]
 * @param {'add'|'result'} [options.mode='add'] - 路径模式
 * @param {string} [options.fallback] - 无字段可推导时的兜底状态；缺省 undefined（不设置）
 * @returns {{ status: string|undefined, applied: boolean }} 应用后的状态与是否发生变更
 */
export function applyToolCallState(existing, updates, options = {}) {
  const { mode = 'add', fallback } = options
  if (!updates) {
    return { status: existing?.status, applied: false }
  }
  // 1. 目标状态推导（唯一推导逻辑）
  const target = _deriveTargetStatus(updates, mode, fallback)
  if (target === undefined) {
    // 无字段可推导：不修改状态
    return { status: existing?.status, applied: false }
  }
  // 2. 新建：直接应用
  if (!existing) {
    return { status: target, applied: true }
  }
  // 3. 终态不回退
  if (isTerminalStatus(existing.status)) {
    return { status: existing.status, applied: false }
  }
  // 4. 合法转换校验（含同状态幂等）
  if (!canTransition(existing.status, target)) {
    return { status: existing.status, applied: false }
  }
  return { status: target, applied: true }
}

/**
 * 审批状态 → 工具执行状态的近似映射（快照恢复 / 兜底展示的唯一推导，Task 7）
 *
 * 场景：刷新后从后端拉取 Approval 历史（无实时事件可纠正）时，需要从
 * approval.state 推导 toolCall.status 的初始值；审批面板兜底展示亦复用。
 * 实时事件路径一律走 applyToolCallState（唯一状态机入口）。
 *
 * 语义（对齐后端 approval_service._persist_and_broadcast 的事件联动：
 * pending 创建 → tool_call_waiting；waiting → tool_call_waiting；
 * processing → tool_call_running）：
 * - pending（待审批）：工具未开始执行 → PENDING（"等待中"，不显示"执行中"）
 * - waiting（本工具已审批，等待同批次其他工具）→ WAITING（"待审批"）
 * - processing / approved（已通过审批，工具开始执行）→ RUNNING
 * - rejected / timeout → 对应终态透传
 *
 * @param {string|undefined} approvalState - approval.state 值
 * @returns {string} ToolCallStatus 枚举值
 */
export function approvalStateToToolStatus(approvalState) {
  switch (approvalState) {
    case ApprovalState.REJECTED: return ToolCallStatus.REJECTED
    case ApprovalState.TIMEOUT: return ToolCallStatus.TIMEOUT
    case ApprovalState.WAITING: return ToolCallStatus.WAITING
    case ApprovalState.PENDING: return ToolCallStatus.PENDING
    default: return ToolCallStatus.RUNNING
  }
}

/**
 * 展示状态推导（组件读取工具卡片状态，无分散三元推导）
 *
 * 优先级：
 *   1. 显式 status（已归一化的状态域值）
 *   2. 事件态 state 映射（EVENT_STATE_TO_STATUS）
 *   3. result / output 有值 → COMPLETED
 *   4. error 有值 → FAILED
 *   5. fallback（默认 RUNNING，与原 ChatMessage 三元推导默认一致）
 *
 * @param {Object|null} toolCall - 工具调用对象（status/state/result/output/error 字段）
 * @param {string} [fallback=ToolCallStatus.RUNNING] - 无字段可推导时的兜底展示状态
 * @returns {string} 展示状态（ToolCallStatus 枚举值）
 */
export function deriveDisplayStatus(toolCall, fallback = ToolCallStatus.RUNNING) {
  if (!toolCall || typeof toolCall !== 'object') return fallback
  const { status, state, result, output, error } = toolCall
  // 1. 显式 status 优先
  if (status !== undefined && status !== null && status !== '') return status
  // 2. 事件态映射
  if (state !== undefined) {
    const mapped = EVENT_STATE_TO_STATUS[state]
    if (mapped !== undefined) return mapped
  }
  // 3. 结果/输出推导（展示层同时兼容 result 与 output 字段）
  if (result != null || output != null) return ToolCallStatus.COMPLETED
  // 4. 错误推导
  if (error != null) return ToolCallStatus.FAILED
  // 5. 兜底
  return fallback
}
