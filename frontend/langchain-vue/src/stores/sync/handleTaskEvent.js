import { logger } from '@/utils/logger'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建 task 通道事件处理器
 *
 * 处理 task WebSocket 通道事件（深度研究独立模式 + 关联 chat 场景兜底路径）。
 *
 * 模块关系：
 * - 深度研究模块与聊天模块相互独立。独立深度研究模式仅走 task 频道，
 *   不涉及 session 频道，确保模块独立性。
 * - 仅"聊天模块的深度研究模式"（关联场景）需要两模块实时同步：
 *   后端同时广播到 session + task 频道，session 频道处理聊天回写并委托更新 taskInfo，
 *   task 频道更新 DeepResearchView 任务状态，形成双路径。
 *
 * taskInfo 更新双路径设计：
 * - 独立深度研究模式（无 chatSessionId）：task 频道是 taskInfo 更新的唯一路径
 * - 关联 chat 场景：task 频道是兜底路径，主路径是 session 频道的 handleStreamCompleted
 *   委托更新（确保 DeepResearchView 未打开时 taskInfo 也实时更新）
 * - 双路径幂等性：updateTaskFromEvent 使用 force:true，重复调用结果一致
 *
 * 与 handleSessionEvent 逻辑对齐，通过 sessionId / taskId 自动路由到 sessionStore 或 researchStore。
 * task 通道是新增通道，事件量较小，不需要 seq 去重和跳号检测。
 * task 通道使用 16 个独立事件类型，通过 event.type 直接区分：
 *   - 7 个工具调用事件（tool_call_*）
 *   - 6 个审批事件（approval_*）
 *   - 1 个 stream_completed（深度研究完成）
 *   - 4 个学习工作流事件（workflow_step / workflow_state_update / workflow_completed / workflow_failed）
 *
 * 工具调用事件和审批事件统一委托给 handleToolCallEvent / handleApprovalEvent，
 * 与 handleSessionEvent 共享同一份处理逻辑（三模块统一）。
 *
 * stream_completed 事件统一调用 researchStore.updateTaskFromEvent 更新任务状态，
 * 确保所有浏览器（含独立深度研究模式）都能收到任务完成/失败事件。
 * 关联 chat 场景下，后端 publish_event_sync 同时广播到 session + task 频道，
 * session 频道触发 handleStreamCompleted 处理聊天消息回写并委托更新 taskInfo（主路径），
 * task 频道触发 researchStore.updateTaskFromEvent 更新 DeepResearchView 任务状态（兜底路径）。
 *
 * 原逻辑位于 sync.js L1127-1169（handleTaskEvent）。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.researchStore - research store 实例
 * @param {(sessionId: string|null, taskId: string|null, payload: Object, eventType: string, source?: string) => Promise<void>} ctx.handleToolCallEvent
 *   - 来自 handleSessionEvent 模块，三模块共享
 * @param {(sessionId: string|null, payload: Object, eventType: string, options?: { taskId?: string, source?: string }) => Promise<void>} ctx.handleApprovalEvent
 *   - 来自 handleSessionEvent 模块，三模块共享
 * @param {(eventType: string, payload: Object, taskId: string) => Promise<void>|void} [ctx.onWorkflowEvent]
 *   - 学习工作流事件回调（learning 模块 4 个 workflow_* 事件），由 sync.js 注入，
 *     内部委托给 workflowStore.updateWorkflowFromEvent；未注入时仅记录日志。
 * @returns {{ handleTaskEvent: (event: RealtimeEvent) => Promise<void> }}
 */
export const createHandleTaskEvent = (ctx) => {
  const { researchStore, handleToolCallEvent, handleApprovalEvent, onWorkflowEvent } = ctx

  /**
   * 处理 task WebSocket 通道事件
   * @param {RealtimeEvent} event
   */
  const handleTaskEvent = async (event) => {
    const taskId = event.payload?.task_id
    if (!taskId) return

    const payload = event.payload || event
    const source = payload.source || 'deep_research'

    switch (event.type) {
      // 7 个工具调用事件类型
      case 'tool_call_pending':
      case 'tool_call_input_ready':
      case 'tool_call_waiting':
      case 'tool_call_running':
      case 'tool_call_completed':
      case 'tool_call_failed':
      case 'tool_call_timeout':
        await handleToolCallEvent(null, taskId, payload, event.type, source)
        break
      // 6 个审批事件类型
      case 'approval_pending':
      case 'approval_processing':
      case 'approval_waiting':
      case 'approval_approved':
      case 'approval_rejected':
      case 'approval_timeout':
        await handleApprovalEvent(null, payload, event.type, { taskId, source })
        break
      case 'stream_completed':
        // 统一底层：task 频道也处理 stream_completed，更新 DeepResearchView 任务状态
        // 关联 chat 场景下 session 频道会同时收到此事件并触发 handleStreamCompleted
        // 处理聊天消息回写；task 频道仅负责更新 researchStore.taskInfo，职责互不重叠。
        // 独立深度研究模式（无 chat_session_id）下，session 频道收不到事件，
        // task 频道是唯一的任务完成事件来源。
        researchStore.updateTaskFromEvent(taskId, payload)
        logger.info(
          `[Sync] task stream_completed: taskId=${taskId}, ` +
          `success=${payload.success !== false}, source=${source}`
        )
        break
      // 学习工作流（learning 模块）4 个事件类型：统一委托给 onWorkflowEvent 回调
      // - workflow_step：节点执行进度（planner/retrieval/quiz_generator/grading/feedback 等）
      // - workflow_state_update：状态变更（waiting_for_answers / completed 等）
      // - workflow_completed：工作流完成（终态）
      // - workflow_failed：工作流失败（终态）
      // 回调由 sync.js 注入，内部调用 workflowStore.updateWorkflowFromEvent 写入 store，
      // WorkflowView watch store 变化后合并到 execution.value，实现跨浏览器同步。
      case 'workflow_step':
      case 'workflow_state_update':
      case 'workflow_completed':
      case 'workflow_failed':
        if (onWorkflowEvent) {
          await onWorkflowEvent(event.type, payload, taskId)
        } else {
          logger.info(
            `[Sync] task ${event.type}: taskId=${taskId}, ` +
            `step=${payload.step || payload.current_step || 'unknown'}, source=${source}`
          )
        }
        break
      default:
        logger.debug(`[Sync] 未处理的 task 事件: ${event.type}`)
    }
  }

  return {
    handleTaskEvent,
  }
}
