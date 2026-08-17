import { logger } from '@/utils/logger'
import { toCamelCase } from '@/utils/sessionTransformers'
import { getEventTaskId } from '@/utils/eventRouting'
import { ResearchTaskStatus } from '@/types'
import { scheduleSubagentsRefresh } from '@/composables/useSubagents'

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
    // 统一入站转换：task 通道 WebSocket 事件 payload snake_case → camelCase
    event.payload = toCamelCase(event.payload)
    // 路由字段解析（P3-R1 同源修复，统一入口 getEventTaskId）：
    // 后端将 task_id 注入事件顶层（见 realtime_events.py _publish_to_task_async），
    // payload 内部不含 task_id。统一 helper 优先 payload，其次 event 顶层，
    // 否则 task 通道事件被静默丢弃。
    const taskId = getEventTaskId(event)
    if (!taskId) return

    const payload = event.payload || event
    const source = payload.source || 'deep_research'

    // subagent_thread_id（spec D10）：事件顶层协议路由标识符（snake_case，不参与
    // camelCase 转换），由 useRealtimeSync.onMessage 还原后保留在 event 顶层。
    // 注入 payload.subagentThreadId（camelCase）供下游 toolCallHandler / approval
    // 归集按子代理 thread 路由。
    if (event.subagent_thread_id) {
      payload.subagentThreadId = event.subagent_thread_id
    }

    switch (event.type) {
      // 工具调用事件类型（每个 EventType 独立 ws_event_name）
      case 'tool_call_pending':
      case 'tool_call_waiting':
      case 'tool_call_rejected':
      case 'tool_call_running':
      case 'tool_call_completed':
      case 'tool_call_failed':
      case 'tool_call_timeout':
        await handleToolCallEvent(null, taskId, payload, event.type, source)
        break
      // 6 个审批事件类型
      // 路由：task 频道审批事件可能关联 chat 会话（聊天触发的深度研究），
      // 后端 payload 携带 cross_module_id（= chat_session_id）。将其作为 sessionId
      // 传入，使审批状态同步更新到消息 toolCalls（详情页展示数据源），
      // 而非仅更新 pendingApprovals —— 否则详情页审批后展示状态不更新（"卡住"）。
      // 独立深度研究（无 chat 关联，cross_module_id 为空）仍走 taskId 分支。
      case 'approval_pending':
      case 'approval_processing':
      case 'approval_waiting':
      case 'approval_approved':
      case 'approval_rejected':
      case 'approval_timeout':
        await handleApprovalEvent(
          payload.crossModuleId || null,
          payload,
          event.type,
          { taskId, source, isReplay: event.isReplay === true }
        )
        break
      case 'stream_completed':
        // 幂等保护：若 researchStore 中该 task 已是终态（completed/failed），跳过
        // 场景：关联 chat 场景下 session 频道已更新 taskInfo，task 频道的 stream_completed
        // 为冗余事件；独立深度研究模式下 task 频道是唯一路径，initial null 不会触发跳过
        {
          const existingTask = researchStore.tasks?.get?.(taskId)
          if (existingTask?.status === ResearchTaskStatus.COMPLETED || existingTask?.status === ResearchTaskStatus.FAILED) {
            logger.debug(
              `[Sync] task stream_completed 幂等跳过（已是终态）: taskId=${taskId}, ` +
              `status=${existingTask.status}`
            )
            break
          }
        }
        researchStore.updateTaskFromEvent(taskId, payload)
        logger.info(
          `[Sync] task stream_completed: taskId=${taskId}, ` +
          `success=${payload.success !== false}, source=${source}`
        )
        break
      case 'status_change':
        // 任务状态实时推送（执行器运行中细粒度状态，Task 4）：
        // running/awaiting_approval/completed/failed + currentStep/finalReport/error，
        // 驱动 DeepResearchView / ResearchTaskDetail 状态标签实时刷新。
        // setTaskStatus 内置终态保护（终态不被滞后非终态覆盖）。
        researchStore.setTaskStatus(taskId, payload)
        logger.info(
          `[Sync] task status_change: taskId=${taskId}, status=${payload.status || 'unknown'}, ` +
          `currentStep=${payload.currentStep || 'unknown'}, source=${source}`
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
            `step=${payload.step || payload.currentStep || 'unknown'}, source=${source}`
          )
        }
        break
      // stream_event（task 通道）：STREAM_REASONING / STREAM_CONTENT_UPDATE 等流式
      // 事件共用 ws_event_name="stream_event"（event_schema._WS_EVENT_NAME_MAP），
      // 必须按 payload.eventType 二次分发（toCamelCase 后 event_type → eventType）。
      // - stream_reasoning：深度研究推理内容（独立深度研究模式）
      // - stream_content_update：主 agent 累计正文（position 内联切段依据，content 非空
      //   工具卡才不堆叠末尾乱序）
      case 'stream_event': {
        const streamEventType = payload.eventType || payload.event_type
        const data = payload.data || payload
        if (streamEventType === 'stream_content_update') {
          if (data.content) {
            researchStore.setTaskStatus(taskId, { content: data.content })
          }
        } else if (streamEventType === 'stream_reasoning') {
          researchStore.setTaskReasoning(taskId, payload)
        }
        break
      }
      // 子代理图层正文/中间思考（task 通道，独立深度研究模式）
      // spec D10：按 subagentThreadId 路由写入 task.subagentContents[threadId]
      case 'stream_subagent_content': {
        const data = payload.data || payload
        const subagentThreadId = payload.subagentThreadId || data.subagentThreadId || ''
        if (subagentThreadId) {
          researchStore.setTaskSubagentContent(taskId, subagentThreadId, data.content || '', data.reasoningContent || '')
        }
        break
      }
      // 子代理状态变更（task 通道，独立深度研究模式）：
      // 与 handleSessionEvent 的 subagent_status_change 分支对称（session 频道
      // 仅覆盖 chat/learning/关联深研；独立深研只走 task 频道，缺失会导致子代理卡
      // 状态停留旧值）。父线程 id 取 payload.sourceId（深研=task_id）回退 taskId。
      case 'subagent_status_change':
        if (payload.subagentThreadId || event.subagent_thread_id) {
          logger.info(
            `[Sync] 子代理状态变更(task): task=${taskId}, subagent=${payload.subagentThreadId || event.subagent_thread_id}, ` +
            `status=${payload.data?.status || payload.status || '(unknown)'}`
          )
          scheduleSubagentsRefresh(payload.sourceId || taskId)
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
