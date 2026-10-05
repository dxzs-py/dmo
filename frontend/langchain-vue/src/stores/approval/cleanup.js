import { useSessionStore } from '@/stores/session'
import { logger } from '@/utils/logger'
import { APPROVAL_EXPIRY_MS, CLEANUP_INTERVAL_MS, PENDING_BIND_QUEUE_TTL_MS } from './constants'

/**
 * 审批超时清理与待绑定队列维护（ARCH-13 拆分）
 *
 * 职责：cleanupExpired 过期审批自动清理、clearByTaskId 按任务清理、
 * clearAll 全量清理（登出）、startCleanup 自动清理定时器、
 * flushPendingBindQueue 待绑定审批队列重绑定。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.pendingApprovals - 统一待审批 Map（ref）
 * @param {Object} ctx.pendingBindQueue - 待绑定审批队列（ref）
 * @param {{ value: number|null }} ctx.cleanupTimerBox - 自动清理定时器句柄容器
 * @returns {{
 *   cleanupExpired: () => void,
 *   clearByTaskId: (taskId: string) => void,
 *   clearAll: () => void,
 *   flushPendingBindQueue: (sessionId?: string) => void,
 *   startCleanup: () => void,
 * }}
 */
export const createCleanupActions = (ctx) => {
  const { pendingApprovals, pendingBindQueue, cleanupTimerBox } = ctx

  /**
   * 清理过期审批（FE-6：优先消费入队时记录的 entry.expiresAt——后端 payload
   * 的真实超时点；缺失回退 createdAt + APPROVAL_EXPIRY_MS 兜底）
   */
  const cleanupExpired = () => {
    const now = Date.now()
    const sessionStore = useSessionStore()
    for (const [key, entry] of pendingApprovals.value.entries()) {
      const expiresAt = entry.expiresAt ?? (entry.createdAt + APPROVAL_EXPIRY_MS)
      if (entry.createdAt && now > expiresAt) {
        logger.warn(`[ApprovalStore] 审批已过期，自动清理: ${key}`)
        pendingApprovals.value.delete(key)
        // 仅当 sessionId 明确有值时才更新 session store
        if (entry.sessionId) {
          sessionStore.updateToolCallApprovalState(entry.sessionId, key, 'timeout')
          sessionStore.setApprovalToLastMessage(entry.sessionId, { ...entry.approvalData, state: 'timeout' })
        }
      }
    }
  }

  /**
   * 清理指定任务 ID 的审批
   * @param {string} taskId
   */
  const clearByTaskId = (taskId) => {
    if (!taskId) return
    for (const [key, entry] of pendingApprovals.value.entries()) {
      if (entry.taskId === taskId) {
        pendingApprovals.value.delete(key)
      }
    }
  }

  /**
   * 清理全部（登出等场景）
   */
  const clearAll = () => {
    pendingApprovals.value.clear()
    if (cleanupTimerBox.value) {
      clearInterval(cleanupTimerBox.value)
      cleanupTimerBox.value = null
    }
  }

  /**
   * 启动自动清理定时器
   */
  const startCleanup = () => {
    if (cleanupTimerBox.value) return
    cleanupTimerBox.value = setInterval(cleanupExpired, CLEANUP_INTERVAL_MS)
  }

  /**
   * 刷新待绑定队列：尝试将队列中的审批绑定到已创建的 toolCall
   * @param {string} [sessionId] - 仅刷新指定 session 的待绑定审批
   */
  const flushPendingBindQueue = (sessionId) => {
    if (pendingBindQueue.value.length === 0) return

    const sessionStore = useSessionStore()
    const remaining = []

    for (const item of pendingBindQueue.value) {
      if (sessionId && item.sessionId !== sessionId) {
        remaining.push(item)
        continue
      }
      const result = sessionStore.setApprovalToToolCall(item.sessionId, item.toolCallId, item.approvalData)
      if (result) {
        logger.info(`[ApprovalStore] 队列重绑定成功: toolCallId=${item.toolCallId}, sessionId=${item.sessionId}`)
      } else {
        remaining.push(item)
      }
    }

    pendingBindQueue.value = remaining

    // 清理超时的队列项
    const now = Date.now()
    pendingBindQueue.value = pendingBindQueue.value.filter(q => now - q.createdAt < PENDING_BIND_QUEUE_TTL_MS)
  }

  return {
    cleanupExpired,
    clearByTaskId,
    clearAll,
    flushPendingBindQueue,
    startCleanup,
  }
}
