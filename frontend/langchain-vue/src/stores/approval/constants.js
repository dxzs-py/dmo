/**
 * approval store 常量（单一来源，ARCH-13 拆分）
 */

/** 审批过期时间：30 分钟 */
export const APPROVAL_EXPIRY_MS = 30 * 60 * 1000

/** 清理检查间隔：5 分钟 */
export const CLEANUP_INTERVAL_MS = 5 * 60 * 1000

/** 待绑定审批队列超时：30 秒（超时未绑定成功即丢弃） */
export const PENDING_BIND_QUEUE_TTL_MS = 30000

/** 审批同步 debounce 间隔：200ms（审批事件到达后立即同步，确保跨浏览器/刷新可恢复） */
export const APPROVAL_SYNC_DEBOUNCE_MS = 200

/**
 * 审批超时提示文案模板（F⑤ 收敛：原 _handleTimeout 内两份重复文案单源化）
 *
 * 使用方式：
 * - _handleTimeout 追加到消息内容：`\n\n> ⏰ ${formatApprovalTimeoutHint(toolName)}\n`
 * - deep_research 来源的 ElMessage.warning：`formatApprovalTimeoutHint(toolName)`
 * - {tool} 为工具名占位符，缺失时回退"未知"
 */
export const APPROVAL_TIMEOUT_HINT = '工具 "{tool}" 的审批已超时，Agent 将使用其他方式继续'

/**
 * 格式化审批超时提示文案（与原两处字面文案逐字一致）
 * @param {string} [toolName] - 工具名（缺失/空时回退"未知"）
 * @returns {string}
 */
export const formatApprovalTimeoutHint = (toolName) =>
  APPROVAL_TIMEOUT_HINT.replace('{tool}', toolName || '未知')
