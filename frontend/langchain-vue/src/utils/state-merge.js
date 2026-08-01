/**
 * 任务/工作流状态合并工具
 *
 * 提供终态保护的状态合并逻辑：终态不被非终态覆盖，force 可强制覆盖。
 * 被 stores/research.js 的 setTaskStatus 与 stores/workflow.js 的 setWorkflowState 共用，
 * 消除两处近乎一致的状态优先级保护代码。
 *
 * 设计为纯函数（无副作用、无日志）：调用方根据返回的 isProtected / changed 自行记录日志，
 * 保留各自的 [Research] / [Workflow] 前缀与日志级别。
 */

/** 任务级终态集合（不可被非终态覆盖） */
export const TASK_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

/**
 * 受保护的状态合并决策
 *
 * 决策规则（与原 setTaskStatus / setWorkflowState 逻辑一致）：
 * - currentStatus 为空（首次设置）→ 使用 freshStatus
 * - freshStatus 为空（fresh 未提供 status）→ 保留 currentStatus
 * - options.force → 强制使用 freshStatus（跳过终态保护，用于权威完成事件）
 * - 当前是终态 + 新状态不是终态 → 保留 currentStatus（终态保护）
 * - 其他 → 使用 freshStatus
 *
 * @param {string} currentStatus - 当前状态（首次设置时可为空）
 * @param {string} freshStatus - 新状态
 * @param {Set<string>} terminalSet - 终态集合
 * @param {Object} [options]
 * @param {boolean} [options.force=false] - 强制覆盖（跳过终态保护）
 * @returns {{ status: string, changed: boolean, isProtected: boolean }}
 *   - status: 最终应使用的 status 值
 *   - changed: status 是否发生变化
 *   - isProtected: 是否触发了终态保护（status 被保留为旧值）
 */
export function mergeProtectedState(currentStatus, freshStatus, terminalSet, options = {}) {
  // 首次设置（currentStatus 为空）：使用新状态
  if (!currentStatus) {
    return { status: freshStatus, changed: Boolean(freshStatus), isProtected: false }
  }
  // 新状态为空：保留当前状态（fresh 未提供 status 字段，合并不应清空 status）
  if (!freshStatus) {
    return { status: currentStatus, changed: false, isProtected: false }
  }
  // force 强制覆盖（跳过终态保护，用于 stream_completed 等权威完成事件）
  if (options.force) {
    return { status: freshStatus, changed: currentStatus !== freshStatus, isProtected: false }
  }
  // 终态保护：当前是终态，新状态不是终态 → 保留当前状态
  if (terminalSet.has(currentStatus) && !terminalSet.has(freshStatus)) {
    return { status: currentStatus, changed: false, isProtected: true }
  }
  // 正常覆盖
  return { status: freshStatus, changed: currentStatus !== freshStatus, isProtected: false }
}
