import { useApprovalStore } from '@/stores/approval'
import { logger } from '@/utils/logger'

/**
 * 统一的审批操作 composable
 *
 * 消除 DeepResearchView 和 ChatView 中 handleApprove/handleReject 的重复模式。
 * 各视图通过 getContext 回调注入视图特定的上下文（taskId、sessionId 等）。
 *
 * @param {Function} [getContext] - 返回审批所需上下文的回调函数，如 { taskId, sessionId }
 * @returns {{ handleApprove: Function, handleReject: Function }}
 *
 * @example
 * // DeepResearchView 中使用
 * const { handleApprove, handleReject } = useApprovalActions(() => ({
 *   taskId: task.value?.task_id,
 *   sessionId: task.value?.session_id,
 * }))
 *
 * // ChatView 中使用
 * const { handleApprove, handleReject } = useApprovalActions(() => ({
 *   sessionId: sessionStore.currentSessionId,
 * }))
 */
export function useApprovalActions(getContext) {
  const approvalStore = useApprovalStore()

  /**
   * 确认审批
   * @param {Object} toolCallData - ToolCallCard emit 的 toolCall 对象（含 approval 字段）
   */
  const handleApprove = async (toolCallData) => {
    const approval = toolCallData?.approval || toolCallData
    if (!approval || approval.state === 'processing') return

    const userInput = toolCallData.user_input ?? toolCallData._user_input
    try {
      await approvalStore.executeApproval(approval, true, userInput, getContext?.() || {})
    } catch (e) {
      logger.error('[useApprovalActions] 确认审批失败:', e)
    }
  }

  /**
   * 拒绝审批
   * @param {Object} toolCallData - ToolCallCard emit 的 toolCall 对象（含 approval 字段）
   */
  const handleReject = async (toolCallData) => {
    const approval = toolCallData?.approval || toolCallData
    if (!approval || approval.state === 'processing') return

    try {
      await approvalStore.executeApproval(approval, false, null, getContext?.() || {})
    } catch (e) {
      logger.error('[useApprovalActions] 拒绝审批失败:', e)
    }
  }

  return { handleApprove, handleReject }
}
