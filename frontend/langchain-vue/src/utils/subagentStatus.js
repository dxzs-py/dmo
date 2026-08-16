/**
 * 子代理状态中文映射（单一权威来源）
 *
 * SubAgentRuntime 四状态闭环 → 前端中文展示，禁止直接抛英文枚举给用户。
 * 枚举值（后端 snake_case 字符串）：
 *   running / completed / failed / interrupted_pending_user_input
 */
export const SUBAGENT_STATUS = {
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  INTERRUPTED_PENDING_USER_INPUT: 'interrupted_pending_user_input',
}

export const SUBAGENT_STATUS_TEXT = {
  [SUBAGENT_STATUS.RUNNING]: '执行中',
  [SUBAGENT_STATUS.INTERRUPTED_PENDING_USER_INPUT]: '等待你的确认',
  [SUBAGENT_STATUS.COMPLETED]: '已完成',
  [SUBAGENT_STATUS.FAILED]: '执行失败',
}

/** 状态 → Element Plus tag type 映射（视觉层，与业务枚举解耦） */
export const SUBAGENT_STATUS_TAG_TYPE = {
  [SUBAGENT_STATUS.RUNNING]: 'warning',
  [SUBAGENT_STATUS.INTERRUPTED_PENDING_USER_INPUT]: 'info',
  [SUBAGENT_STATUS.COMPLETED]: 'success',
  [SUBAGENT_STATUS.FAILED]: 'danger',
}

/** 状态 → 摘要卡片图标（7.md 设计稿：执行中 ⏳ / 等待确认 ⚠️ / 已完成 ✅ / 失败 ❌） */
export const SUBAGENT_STATUS_ICON = {
  [SUBAGENT_STATUS.RUNNING]: '⏳',
  [SUBAGENT_STATUS.INTERRUPTED_PENDING_USER_INPUT]: '⚠️',
  [SUBAGENT_STATUS.COMPLETED]: '✅',
  [SUBAGENT_STATUS.FAILED]: '❌',
}

export const getSubagentStatusText = (status) => SUBAGENT_STATUS_TEXT[status] || status || ''

export const getSubagentStatusTagType = (status) => SUBAGENT_STATUS_TAG_TYPE[status] || 'info'

export const getSubagentStatusIcon = (status) => SUBAGENT_STATUS_ICON[status] || '•'

export const isPendingUserInput = (status) => status === SUBAGENT_STATUS.INTERRUPTED_PENDING_USER_INPUT
