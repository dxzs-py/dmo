import { useSessionStore } from '@/stores/session'
import { getInterruptId } from '@/utils/messageOperations'
import { logger } from '@/utils/logger'

/**
 * 审批快照/历史恢复（ARCH-13 拆分）
 *
 * 职责：页面刷新/重连后从会话消息或 SSE 历史审批数据重建 pendingApprovals。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.pendingApprovals - 统一待审批 Map（ref）
 * @returns {{
 *   restoreFromSession: (sessionId: string) => void,
 *   restoreFromSSEHistory: (approvalData: Object, taskId: string, sessionId?: string|null) => void,
 * }}
 */
export const createRestoreActions = (ctx) => {
  const { pendingApprovals } = ctx

  /**
   * 从聊天会话消息中恢复 pendingApprovals
   * 页面刷新后 toolCalls 已从后端恢复，但 pendingApprovals 是运行时 Map，需要重建
   * @param {string} sessionId
   */
  const restoreFromSession = (sessionId) => {
    const sessionStore = useSessionStore()

    // 清理不属于当前会话的旧条目
    for (const [key, entry] of pendingApprovals.value.entries()) {
      if (entry.sessionId && entry.sessionId !== sessionId) {
        pendingApprovals.value.delete(key)
      }
    }

    // 从当前会话消息恢复
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return
    for (const msg of session.messages) {
      if (!msg.toolCalls || !Array.isArray(msg.toolCalls)) continue
      for (const tc of msg.toolCalls) {
        const approval = tc.approval
        if (approval && approval.state === 'pending') {
          const toolCallId = getInterruptId(approval) || tc.id || ''
          if (toolCallId && !pendingApprovals.value.has(toolCallId)) {
            const isDeepResearch = approval.source === 'deep_research'
            pendingApprovals.value.set(toolCallId, {
              source: isDeepResearch ? 'deep_research' : 'chat',
              taskId: approval.taskId || null,
              sessionId,
              approvalData: approval,
              createdAt: Date.now(),
            })
          }
        }
      }
    }
  }

  /**
   * 从 SSE 历史审批数据恢复（深度研究模块刷新/重连场景）
   * 后端 views_stream.py 在 SSE 连接建立时推送 Redis List 中的历史审批
   * @param {Object} approvalData - 历史审批数据
   * @param {string} taskId - 深度研究任务 ID
   * @param {string} [sessionId] - 聊天会话 ID（聊天模块恢复时传入，用于同步到 sessionStore）
   */
  const restoreFromSSEHistory = (approvalData, taskId, sessionId = null) => {
    const toolCallId = getInterruptId(approvalData)
    if (!toolCallId) {
      logger.warn('[ApprovalStore] restoreFromSSEHistory: 无法提取 toolCallId', approvalData)
      return
    }
    // 已存在则跳过（可能已通过实时推送收到）
    if (pendingApprovals.value.has(toolCallId)) {
      logger.debug(`[ApprovalStore] restoreFromSSEHistory: 审批已存在，跳过 ${toolCallId}`)
      return
    }

    const state = approvalData.state || 'pending'
    const effectiveSessionId = sessionId || null

    // 已处理/超时的审批：不加入 pendingApprovals（不需要用户操作），但更新 session store 的 UI 状态
    if (state === 'timeout' || state === 'approved' || state === 'rejected') {
      logger.info(`[ApprovalStore] restoreFromSSEHistory: 审批已终态(${state}), 更新 UI: ${toolCallId}`)
      if (effectiveSessionId) {
        const sessionStore = useSessionStore()
        sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, state)
        sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...approvalData, state })
      }
      return
    }

    logger.info(`[ApprovalStore] restoreFromSSEHistory: 恢复审批 ${toolCallId}, taskId=${taskId}, tool=${approvalData.toolName}`)

    pendingApprovals.value.set(toolCallId, {
      source: 'deep_research',
      taskId,
      sessionId: effectiveSessionId,
      approvalData: { ...approvalData, state: 'pending' },
      createdAt: Date.now(),
    })

    // 同步到 session store（聊天模块恢复时需要，确保消息中的审批 UI 可见）
    if (effectiveSessionId) {
      const sessionStore = useSessionStore()
      sessionStore.setApprovalToToolCall(effectiveSessionId, toolCallId, { ...approvalData, state: 'pending' })
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...approvalData, state: 'pending' })
    }
  }

  return {
    restoreFromSession,
    restoreFromSSEHistory,
  }
}
