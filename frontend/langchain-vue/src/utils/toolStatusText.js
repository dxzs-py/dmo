/**
 * 工具调用状态 → 文案 / 标签色映射（单一权威）
 *
 * 收敛 ToolCallCard.vue / ChatRightPanel.vue 两处重复的状态映射，避免
 * 文案与标签色在不同组件间漂移。
 *
 * 口径基线：以 ToolCallCard 的 7 态（completed/failed/rejected/running/
 * waiting/timeout/pending）为权威；ChatRightPanel 13 键中超出 7 态的键
 * （input-available/output-available/output-error/calling/success/error/
 * approved/pending-approval/processing）保留在双表中。
 *
 * 权威判定依据（合并时两表冲突取 ToolCallCard 版）：
 *   - rejected → '已拒绝'（ToolCallCard 版本）
 *   - timeout 标签色 → 'danger'（ToolCallCard 版本，ChatRightPanel 原为 warning）
 */
/** 工具调用状态 → 中文文案 */
export const TOOL_STATUS_TEXT = {
  // —— ToolCallCard 权威 7 态 ——
  'completed': '已完成',
  'failed': '失败',
  'rejected': '已拒绝',
  'running': '执行中',
  'waiting': '待审批',
  'timeout': '已超时',
  'pending': '等待中',
  // —— ChatRightPanel 超出 7 态的补充键 ——
  'input-available': '等待中',
  'output-available': '已完成',
  'output-error': '出错',
  'calling': '调用中',
  'success': '成功',
  'error': '失败',
  'approved': '已确认',
  'pending-approval': '待审批',
  'processing': '处理中',
}

/** 工具调用状态 → Element Plus tag type */
export const TOOL_STATUS_TAG_TYPE = {
  // —— ToolCallCard 权威 7 态 ——
  'completed': 'success',
  'failed': 'danger',
  'rejected': 'danger',
  'running': 'warning',
  'waiting': 'info',
  'timeout': 'danger',
  'pending': 'info',
  // —— ChatRightPanel 超出 7 态的补充键 ——
  'input-available': 'warning',
  'output-available': 'success',
  'output-error': 'danger',
  'calling': 'warning',
  'success': 'success',
  'error': 'danger',
  'approved': 'success',
  'pending-approval': 'warning',
  'processing': 'warning',
}

/**
 * 工具调用状态 → 中文文案（未知状态兜底 '等待中'，与 ToolCallCard 原行为一致）
 * @param {string} status
 * @returns {string}
 */
export const getToolStatusText = (status) => TOOL_STATUS_TEXT[status] || '等待中'

/**
 * 工具调用状态 → Element Plus tag type（未知状态兜底 'info'）
 * @param {string} status
 * @returns {string}
 */
export const getToolStatusTagType = (status) => TOOL_STATUS_TAG_TYPE[status] || 'info'
