import { useApprovalStore } from '@/stores/approval'
import { useSessionStore } from '@/stores/session'
import { ElMessage } from 'element-plus'
import { getInterruptId } from '@/utils/message-operations'

/**
 * 深度研究审批 composable
 *
 * 封装 ToolCallCard 的确认/拒绝审批逻辑：
 *   - handleApprove / handleReject：调用 approvalStore.executeApproval，
 *     防重复（processing 态拦截），失败时恢复 session store 中的审批状态
 *
 * taskPendingApprovals（基于 approvalStore.pendingApprovals 过滤当前任务的待审批条目）
 * 由 useResearchTask 导出，避免重复定义。
 *
 * 实时审批事件由 WebSocket 推送（syncStore.handleRealtimeEvent），
 * 本 composable 仅处理用户主动确认/拒绝操作。
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.task - 当前任务 ref
 * @returns {{
 *   handleApprove: (toolCallData: Object) => Promise<void>,
 *   handleReject: (toolCallData: Object) => Promise<void>,
 * }}
 */
export function useResearchApproval({ task }) {
  const approvalStore = useApprovalStore()

  /**
   * 失败时恢复 session store 中的审批状态为 pending
   * @param {string} interruptId
   * @param {Object} approval
   */
  const _restorePendingState = (interruptId, approval) => {
    const sessionStore = useSessionStore()
    const sid = sessionStore.currentSessionId
    if (sid) {
      sessionStore.updateToolCallApprovalState(sid, interruptId, 'pending')
      sessionStore.setApprovalToLastMessage(sid, { ...approval, state: 'pending' })
    }
  }

  /**
   * 确认审批（ToolCallCard emit 的参数是 toolCall 对象）
   * @param {Object} toolCallData
   */
  const handleApprove = async (toolCallData) => {
    const approval = toolCallData.approval || toolCallData
    const interruptId = getInterruptId(approval) || toolCallData.id
    const userInput = toolCallData._user_input

    // 防重复：如果正在处理中，忽略（executeApproval 内部也有防重复，此处提前拦截避免无效调用）
    const entry = approvalStore.pendingApprovals.get(interruptId)
    if (entry?.approvalData?.state === 'processing') return

    try {
      await approvalStore.executeApproval(approval, true, userInput, {
        taskId: task.value?.task_id,
      })
      ElMessage.success('已确认操作')
    } catch {
      // 恢复审批状态（executeApproval 内部 catch 已恢复 pendingApprovals，此处同步 session store）
      _restorePendingState(interruptId, approval)
      ElMessage.error('确认操作失败')
    }
  }

  /**
   * 拒绝审批（ToolCallCard emit 的参数是 toolCall 对象）
   * @param {Object} toolCallData
   */
  const handleReject = async (toolCallData) => {
    const approval = toolCallData.approval || toolCallData
    const interruptId = getInterruptId(approval) || toolCallData.id

    // 防重复：如果正在处理中，忽略
    const entry = approvalStore.pendingApprovals.get(interruptId)
    if (entry?.approvalData?.state === 'processing') return

    try {
      await approvalStore.executeApproval(approval, false, null, {
        taskId: task.value?.task_id,
      })
      ElMessage.info('已拒绝操作')
    } catch {
      // 恢复审批状态（executeApproval 内部 catch 已恢复 pendingApprovals，此处同步 session store）
      _restorePendingState(interruptId, approval)
      ElMessage.error('拒绝操作失败')
    }
  }

  return {
    handleApprove,
    handleReject,
  }
}
