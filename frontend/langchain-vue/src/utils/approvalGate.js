/**
 * 审批控件置灰判定（统一 chat / research，消除「isFinalized || !isLast」与
 * 「status !== AWAITING_APPROVAL」的分叉）
 *
 * 统一规则：仅「活跃版本 + 未固化 + 末尾轮次」可交互，其余一律置灰。
 * - chat：message.isFinalized（固化）+ isLast（末尾轮次）
 * - research：任务终态（completed/failed）视为固化；单任务无轮次概念，isLast 恒 true
 *
 * @param {Object} [opts]
 * @param {boolean} [opts.isFinalized=false] - 是否已固化（消息固化 / 任务终态）
 * @param {boolean} [opts.isLast=true] - 是否末尾轮次（消息维度；任务维度恒 true）
 * @returns {boolean} true 表示置灰（不可交互）
 */
export function isApprovalDisabled({ isFinalized = false, isLast = true } = {}) {
  if (isFinalized) return true
  if (!isLast) return true
  return false
}
