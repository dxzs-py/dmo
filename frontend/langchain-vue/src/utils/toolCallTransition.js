/**
 * 前端工具调用状态转换工具（单一真相源）
 *
 * 对应后端 tool_call_lifecycle.py 的 _VALID_TRANSITIONS。
 * 集中管理工具调用状态的转换规则、优先级、保护集合，
 * 用于处理 WebSocket 事件乱序到达的防御性状态保护。
 *
 * 层次关系：
 *   - 后端 tool_call_lifecycle.py：权威状态机，验证转换并发布事件
 *   - 前端本模块：防御层，处理事件乱序 / 丢失 / 快照校对
 *
 * 声明式转换映射（对应后端 _VALID_TRANSITIONS）：
 *
 *   PENDING   → WAITING / RUNNING / FAILED / TIMEOUT (+ self)
 *   WAITING   → RUNNING / COMPLETED / FAILED / TIMEOUT / REJECTED (+ self)
 *   RUNNING   → COMPLETED / FAILED / TIMEOUT (+ self)
 *   终态      → 不可再转换
 */

import { ToolCallStatus } from '@/types'

// ==================== 转换映射（对应后端 _VALID_TRANSITIONS） ====================

/** @type {Record<string, Set<string>>} */
const _TRANSITION_MAP = {
  [ToolCallStatus.PENDING]: new Set([
    ToolCallStatus.WAITING,
    ToolCallStatus.RUNNING,
    ToolCallStatus.FAILED,
    ToolCallStatus.TIMEOUT,
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

// ==================== 终态集合 ====================

/** 工具调用终态（不可再转换） */
const _TERMINAL_STATUSES = new Set([
  ToolCallStatus.COMPLETED,
  ToolCallStatus.FAILED,
  ToolCallStatus.TIMEOUT,
  ToolCallStatus.REJECTED,
])

// ==================== 公共 API ====================

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
  // 同状态允许
  if (fromStatus === toStatus) return true
  // 终态不可逆
  if (_TERMINAL_STATUSES.has(fromStatus)) return false
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
export function toolCallStatusPriority(status) {
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
 * 非终态状态集合
 *
 * 用途：流式完成时兜底修正仍处于非终态的工具调用。
 * 见 stores/sync/messageIntegrity.js finalizeToolCallsForCompletedMessage。
 */
export const NON_TERMINAL_STATUSES = new Set([
  ToolCallStatus.PENDING,
  ToolCallStatus.RUNNING,
  ToolCallStatus.WAITING,
])

/**
 * 受保护状态集合
 *
 * 这些状态不应被 SSE/WebSocket 事件覆盖：
 *   - COMPLETED / FAILED / TIMEOUT / REJECTED：终态不可逆
 *   - WAITING：需结合 canTransition 检查是否允许向执行态转换
 *
 * 使用方式：
 *   if (PROTECTED_STATUSES.has(existing.status) && existing.approval) {
 *     if (!canTransition(existing.status, incoming.status)) {
 *       // 拒绝覆盖
 *     }
 *   }
 */
export const PROTECTED_STATUSES = new Set([
  ToolCallStatus.COMPLETED,
  ToolCallStatus.FAILED,
  ToolCallStatus.TIMEOUT,
  ToolCallStatus.REJECTED,
  ToolCallStatus.WAITING,
])
